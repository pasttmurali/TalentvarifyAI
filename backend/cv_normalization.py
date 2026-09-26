"""Deterministic normalization, anti-hallucination validation, and schema enforcement for CV parsing."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

SYSTEM_EXTRACTION_INSTRUCTION = """You are a deterministic resume/CV information extraction engine.

Your job is to transcribe and structurally classify information explicitly supported by the supplied CV.

You are NOT allowed to improve the resume.
You are NOT allowed to invent missing information.
You are NOT allowed to infer certifications, projects, employers, qualifications, dates, languages, achievements, or skills without evidence in the CV.

Preserve the candidate's wording wherever practical.

Return only data supported by the resume.

If a field is not present, return null, empty string, or empty array according to the provided schema.

Distinguish carefully between:

1. programming languages
2. technical/domain skills (hard/functional skills for any industry)
3. soft skills (interpersonal, behavioral, or cognitive attributes)
4. spoken/written human languages
5. work experience
6. projects
7. education
8. certifications/courses
9. personal information
10. social links

UNIVERSAL CV RULE FOR SOFT SKILLS EXTRACTION:
The soft-skill extraction logic must be domain-independent and must work for CVs from ANY profession or industry (Software/IT, AI/ML, Engineering, Finance/Accounting, Banking, Sales, Marketing, HR, Admin, Healthcare, Education, Hospitality, Manufacturing, Construction, Legal, Supply Chain, Operations, Customer Service, Management, Students/Fresh Grads, etc.).

Do not classify skills based on simple technology blacklists. Instead, classify every candidate skill semantically:

1. Is this primarily a technical, functional, professional, domain, operational, managerial-discipline, tool, software, methodology, certification, or job-specific competency?
   (Examples: Financial Reporting, Patient Care, Digital Marketing, Inventory Management, Recruitment, Project Management, AutoCAD, Data Analysis, Accounting, Budgeting, Sales Strategy, Supply Chain Management)
   → YES: Include in technical_skills. Do NOT include in soft_skills.

2. Does this primarily describe how the person communicates, collaborates, leads people, thinks, adapts, organizes themselves, builds relationships, resolves interpersonal situations, or behaves at work?
   (Examples: Communication, Teamwork, Leadership, Adaptability, Negotiation, Conflict Resolution, Problem Solving, Critical Thinking, Time Management, Attention to Detail, Interpersonal Skills, Active Listening, Emotional Intelligence)
   → YES: Include in soft_skills.

3. Never invent a soft skill simply because a job responsibility seems to require it.
4. When uncertain, exclude the skill from soft_skills rather than incorrectly classifying a domain or functional skill as a soft skill.

A technology mentioned inside a project may be included in project technologies and technical skills.
A technology mentioned inside an employment responsibility may be included in skills_used and technical skills.
However, a technology must NEVER be turned into a certification.
A certification must only exist if the CV explicitly presents it as a certification, credential, course certificate, licence, training credential, or clearly equivalent item.
A project must only exist if a project or project-like body of work is explicitly identifiable from the CV.

Return valid JSON matching the provided schema exactly."""

EXTRACTION_SCHEMA_JSON = """{
  "personal_info": {
    "full_name": null,
    "email": null,
    "phone": null,
    "location": null,
    "professional_title": null,
    "headline": null
  },
  "professional_summary": "",
  "programming_languages": [],
  "human_languages": [
    {
      "language": "",
      "speaking_level": null,
      "reading_level": null,
      "writing_level": null
    }
  ],
  "technical_skills": [],
  "soft_skills": [],
  "experience": [
    {
      "company": "",
      "role": "",
      "start_date": null,
      "end_date": null,
      "is_current": false,
      "description": "",
      "responsibilities": [],
      "skills_used": [],
      "achievements": []
    }
  ],
  "projects": [
    {
      "name": "",
      "description": "",
      "technologies": [],
      "soft_skills": [],
      "github_url": null,
      "url": null,
      "achievements": []
    }
  ],
  "education": [
    {
      "qualification": "",
      "institution": "",
      "field": "",
      "start_year": null,
      "end_year": null,
      "grade": null
    }
  ],
  "certifications": [
    {
      "name": "",
      "issuer": null,
      "description": "",
      "relevant_skills": [],
      "issue_date": null,
      "expiry_date": null,
      "credential_id": null,
      "credential_url": null
    }
  ],
  "social_links": {
    "linkedin_url": null,
    "github_url": null,
    "portfolio_url": null
  }
}"""

COMMON_PROGRAMMING_LANGUAGES: Set[str] = {
    "python", "java", "javascript", "typescript", "c", "c++", "c#", "go", "golang", "rust",
    "swift", "kotlin", "php", "ruby", "sql", "r", "matlab", "dart", "scala", "perl",
    "haskell", "lua", "powershell", "bash", "shell", "assembly", "html", "css"
}

COMMON_HUMAN_LANGUAGES: Set[str] = {
    "english", "tamil", "sinhala", "french", "spanish", "german", "japanese", "mandarin",
    "chinese", "hindi", "arabic", "korean", "italian", "portuguese", "russian", "dutch",
    "bengali", "urdu", "malay", "swedish", "norwegian", "danish", "finnish", "greek",
    "turkish", "polish", "czech", "hungarian", "romanian", "ukrainian", "hebrew",
    "thai", "vietnamese", "indonesian", "tagalog", "persian", "farsi", "swahili",
    "cantonese", "punjabi", "gujarati", "marathi", "telugu", "kannada", "malayalam",
    "nepali", "burmese", "khmer", "sinhalese", "afrikaans", "catalan", "serbian",
    "croatian", "slovak", "bulgarian", "slovenian", "latvian", "lithuanian", "estonian",
    "albanian", "macedonian", "maltese", "icelandic", "welsh", "irish",
}

LANGUAGE_NAME_NORM_MAP: Dict[str, str] = {
    "english language": "English",
    "tamil language": "Tamil",
    "sinhalese": "Sinhala",
    "sinhala language": "Sinhala",
    "mandarin": "Mandarin Chinese",
    "chinese - mandarin": "Mandarin Chinese",
    "mandarin (chinese)": "Mandarin Chinese",
    "mandarin chinese": "Mandarin Chinese",
    "chinese": "Chinese",
    "french language": "French",
    "german language": "German",
    "spanish language": "Spanish",
    "dutch language": "Dutch",
    "italian language": "Italian",
    "japanese language": "Japanese",
    "korean language": "Korean",
    "arabic language": "Arabic",
    "hindi language": "Hindi",
    "russian language": "Russian",
}

CERTIFICATION_KEYWORDS: Set[str] = {
    "certified", "certificate", "certification", "specialization", "course", "licence",
    "license", "credential", "training", "diploma", "accredited", "exam", "bootcamp"
}

# Proficiency keyword → standardised level label (ordered from high to low)
PROFICIENCY_KEYWORDS: Dict[str, str] = {
    "native": "Native",
    "mother tongue": "Native",
    "first language": "Native",
    "fluent": "Fluent",
    "professional": "Fluent",
    "full professional": "Fluent",
    "business": "Fluent",
    "advanced": "Advanced",
    "upper intermediate": "Advanced",
    "upper-intermediate": "Advanced",
    "intermediate": "Intermediate",
    "moderate": "Intermediate",
    "conversational": "Conversational",
    "limited working": "Basic",
    "elementary": "Basic",
    "basic": "Basic",
    "beginner": "Basic",
    "a1": "Basic", "a2": "Basic",
    "b1": "Intermediate", "b2": "Advanced",
    "c1": "Fluent", "c2": "Native",
}

MONTH_MAP: Dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12
}

# Functional / domain / technical / managerial competencies that must NEVER be placed in soft_skills
FUNCTIONAL_DOMAIN_KEYWORDS: Set[str] = {
    "project management", "program management", "product management", "financial reporting",
    "patient care", "digital marketing", "inventory management", "recruitment", "talent acquisition",
    "autocad", "data analysis", "business analysis", "accounting", "auditing", "budgeting",
    "sales strategy", "supply chain management", "quality assurance", "devops", "cybersecurity",
    "risk management", "contract negotiation", "vendor management", "market research",
    "payroll", "strategic planning", "customer service management", "event management",
    "graphic design", "seo", "sem", "copywriting", "content writing", "social media management",
    "clinical care", "nursing", "underwriting", "portfolio management", "taxation",
    "solution architecture", "data strategy", "model development", "model deployment",
    "production integration", "model optimization", "model tuning", "error analysis",
    "annotation qa", "dataset quality control", "data preprocessing", "exploratory data analysis",
    "feature engineering", "feature compacting", "database design", "backend development",
    "software engineering", "pre-sales", "product roadmapping", "customer requirements analysis",
    "requirements translation", "requirements analysis", "process improvement",
    "oop", "solid", "agile development", "agile", "scrum", "end-to-end delivery",
    "technical proposal development", "solution planning"
}

# Standard behavioral / interpersonal / cognitive soft skills
RECOGNIZED_SOFT_SKILLS: Set[str] = {
    "communication", "teamwork", "collaboration", "leadership", "adaptability",
    "negotiation", "conflict resolution", "problem solving", "problem-solving",
    "critical thinking", "time management", "attention to detail", "interpersonal skills",
    "active listening", "emotional intelligence", "decision making", "decision-making",
    "creativity", "patience", "empathy", "resilience", "work ethic", "flexibility",
    "accountability", "motivation", "self-motivation", "stress management", "persuasion",
    "relationship building", "stakeholder management", "team management", "people management",
    "mentorship", "coaching", "organization", "organizational skills", "client communication",
    "stakeholder communication", "stakeholder alignment", "cross-functional collaboration"
}

# Comprehensive Alias Mapping to Canonical Skill Names
SKILL_ALIAS_MAP: Dict[str, str] = {
    # Web & Frameworks
    "reactjs": "React", "react.js": "React", "react js": "React", "react": "React",
    "nodejs": "Node.js", "node.js": "Node.js", "node js": "Node.js", "node": "Node.js",
    "expressjs": "Express", "express.js": "Express", "express js": "Express", "express": "Express",
    "mongodb": "MongoDB", "mongo db": "MongoDB", "mongo": "MongoDB",
    "mysql": "MySQL", "my sql": "MySQL",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "postgre sql": "PostgreSQL",
    "flask": "Flask", "django": "Django", "nestjs": "NestJS", "nest.js": "NestJS", "nest js": "NestJS",
    "laravel": "Laravel", "gunicorn": "Gunicorn",
    
    # AI / ML / Data Science
    "pytorch": "PyTorch", "py torch": "PyTorch",
    "tensorflow": "TensorFlow", "tensor flow": "TensorFlow", "tf": "TensorFlow",
    "keras": "Keras",
    "sklearn": "Scikit-learn", "scikit learn": "Scikit-learn", "scikit-learn": "Scikit-learn",
    "opencv": "OpenCV", "open cv": "OpenCV",
    "yolo": "YOLO", "yolov8": "YOLO", "yolov5": "YOLO",
    "shap": "SHAP", "lime": "LIME",
    "gradcam": "Grad-CAM", "grad-cam": "Grad-CAM",
    "mobilenet": "MobileNet", "mobilenetv2": "MobileNet",
    "gans": "GANs", "gan": "GANs", "generative adversarial networks": "GANs",
    "cnn": "CNNs", "cnns": "CNNs", "convolutional neural networks": "CNNs",
    "rnn": "RNN", "rnns": "RNN", "recurrent neural networks": "RNN",
    "lstm": "LSTM", "bert": "BERT", "llm": "LLM", "llms": "LLM", "large language models": "LLM",
    
    # Cloud & DevOps
    "aws": "AWS", "amazon web services": "AWS",
    "gcp": "GCP", "google cloud platform": "GCP", "google cloud": "GCP",
    "azure": "Azure", "microsoft azure": "Azure",
    "docker": "Docker", "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "git": "Git", "github": "GitHub", "gitlab": "GitLab",
    "dvc": "DVC", "mlflow": "MLflow", "wandb": "WandB", "weights & biases": "WandB", "weights and biases": "WandB",
    "ci/cd": "CI/CD", "cicd": "CI/CD", "ci/cd pipeline": "CI/CD", "ci/cd pipelines": "CI/CD",
    "continuous integration": "CI/CD", "continuous deployment": "CI/CD",
    "continuous integration / continuous deployment": "CI/CD", "continuous integration/continuous deployment": "CI/CD",
    
    # APIs & Protocols
    "rest api": "REST APIs", "rest apis": "REST APIs", "restful api": "REST APIs", "restful apis": "REST APIs", "rest": "REST APIs",
    "graphql": "GraphQL", "grpc": "gRPC",
    
    # Concepts & Competencies
    "xai": "Explainable AI", "explainable ai": "Explainable AI",
    "object detection": "Object Detection", "object detection models": "Object Detection",
    "image segmentation": "Image Segmentation", "image segmentation models": "Image Segmentation",
    "image classification": "Image Classification",
    "transfer learning": "Transfer Learning",
    "eda": "Exploratory Data Analysis", "exploratory data analysis": "Exploratory Data Analysis",
    "feature engineering": "Feature Engineering",
    "feature compacting": "Feature Compacting",
    "data preprocessing": "Data Preprocessing",
    "gpu inference": "GPU Inference",
    "image processing": "Image Processing",
    "data analysis": "Data Analysis",
    "algorithms": "Algorithms",
    "data structures": "Data Structures",
    "database design": "Database Design",
    "backend development": "Backend Development",
    "software engineering": "Software Engineering",
    "oop": "OOP", "object-oriented programming": "OOP", "object oriented programming": "OOP",
    "solid": "SOLID", "solid principles": "SOLID",
    "agile": "Agile", "agile development": "Agile Development", "agile methodology": "Agile Development", "scrum": "Scrum",
    "deep learning": "Deep Learning", "machine learning": "Machine Learning", "ml": "Machine Learning",
    "nlp": "Natural Language Processing", "natural language processing": "Natural Language Processing",
    "computer vision": "Computer Vision",
    
    # Soft & Domain Skills
    "leadership": "Leadership",
    "team management": "Team Management", "people management": "Team Management",
    "collaboration": "Collaboration",
    "communication": "Communication",
    "client communication": "Client Communication",
    "stakeholder communication": "Stakeholder Communication",
    "stakeholder alignment": "Stakeholder Alignment",
    "problem solving": "Problem Solving", "problem-solving": "Problem Solving",
    "analytical thinking": "Analytical Thinking",
    "cross-functional collaboration": "Cross-functional Collaboration",
    "pre-sales": "Pre-sales",
    "solution architecture": "Solution Architecture",
    "data strategy": "Data Strategy",
    "model development": "Model Development",
    "model deployment": "Model Deployment",
    "production integration": "Production Integration",
    "model optimization": "Model Optimization",
    "model tuning": "Model Tuning",
    "error analysis": "Error Analysis",
    "annotation qa": "Annotation QA",
    "dataset quality control": "Dataset Quality Control",
    "requirements analysis": "Requirements Analysis",
    "requirements translation": "Requirements Translation",
    "product roadmapping": "Product Roadmapping",
    "end-to-end delivery": "End-to-end Delivery",
    "technical proposal development": "Technical Proposal Development",
    "solution planning": "Solution Planning",
    "process improvement": "Process Improvement",
}

CERTIFICATION_PROVIDERS: Set[str] = {
    "coursera", "udemy", "edx", "linkedin learning", "pluralsight", "datacamp",
    "amazon web services", "aws", "google", "gcp", "google cloud", "microsoft",
    "azure", "oracle", "cisco", "ibm", "stanford university", "deeplearning.ai",
    "meta", "salesforce", "hashicorp"
}

def canonicalize_skill_name(raw_name: Any) -> str:
    """Normalize a skill name into its standard canonical representation."""
    if not raw_name:
        return ""
    if isinstance(raw_name, dict):
        raw_name = raw_name.get("skill") or raw_name.get("name") or ""
    clean = clean_str(raw_name)
    if not clean:
        return ""
    lowered = clean.casefold()
    if lowered in SKILL_ALIAS_MAP:
        return SKILL_ALIAS_MAP[lowered]
    
    clean_no_suffix = re.sub(r"\s+(?:framework|library|models?|pipelines?|tool|technology)$", "", clean, flags=re.IGNORECASE).strip()
    if clean_no_suffix and clean_no_suffix.casefold() in SKILL_ALIAS_MAP:
        return SKILL_ALIAS_MAP[clean_no_suffix.casefold()]
        
    return clean


def is_soft_skill_candidate(skill_name: str) -> bool:
    """Semantically determine if a skill is a soft skill according to the Universal CV Rule."""
    if not skill_name or not str(skill_name).strip():
        return False
    val_lower = str(skill_name).strip().casefold()

    # Rule 1: Check if explicitly a functional/domain competency
    if val_lower in FUNCTIONAL_DOMAIN_KEYWORDS:
        return False

    # Check for recognized soft skills
    if val_lower in RECOGNIZED_SOFT_SKILLS:
        return True

    # Rule 1: Check for functional domain substrings
    domain_suffixes = {"reporting", "analysis", "marketing", "accounting", "auditing", "care", "design", "development", "engineering", "recruitment", "nursing"}
    if any(suffix in val_lower for suffix in domain_suffixes):
        return False

    # Generic "management" without being team/people/time management is domain (e.g. Project Management, Risk Management)
    if "management" in val_lower and val_lower not in {"time management", "stakeholder management", "team management", "people management"}:
        return False

    # Rule 4: When uncertain, exclude from soft_skills per rule 4
    return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_url(value: Any) -> Optional[str]:
    if not value:
        return None
    val_str = re.sub(r"\s+", "", str(value).strip()).rstrip(r".,;:)>}]'\"")
    if not val_str:
        return None
    if not re.match(r"^https?://", val_str, re.IGNORECASE):
        if re.match(r"^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(/.*)?$", val_str):
            val_str = f"https://{val_str}"
    try:
        parsed = urlparse(val_str)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return val_str
    except Exception:
        pass
    return None


def parse_date_components(value: Any, is_end: bool = False, is_current: bool = False) -> Optional[Tuple[int, int]]:
    """Parse a date into (year, month) deterministically."""
    if is_current:
        now = utcnow()
        return now.year, now.month
    if not value or not str(value).strip():
        if is_end:
            now = utcnow()
            return now.year, now.month
        return None
    val_str = str(value).strip()
    if re.search(r"\b(present|current|now|ongoing|today|till date)\b", val_str, re.IGNORECASE):
        now = utcnow()
        return now.year, now.month

    # Check for Month Name + Year e.g. "May 2021", "January, 2020", "2021 May"
    m_name = re.search(r"\b([a-zA-Z]{3,9})[,\s/]+(\d{4})\b", val_str)
    if m_name:
        m_str = m_name.group(1).lower()
        if m_str in MONTH_MAP:
            return int(m_name.group(2)), MONTH_MAP[m_str]

    m_name_rev = re.search(r"\b(\d{4})[,\s/]+([a-zA-Z]{3,9})\b", val_str)
    if m_name_rev:
        m_str = m_name_rev.group(2).lower()
        if m_str in MONTH_MAP:
            return int(m_name_rev.group(1)), MONTH_MAP[m_str]

    # Check for YYYY-MM or YYYY/MM
    m_ym = re.search(r"\b(\d{4})[-/.](\d{1,2})\b", val_str)
    if m_ym:
        year = int(m_ym.group(1))
        month = min(12, max(1, int(m_ym.group(2))))
        return year, month

    # Check for MM-YYYY or MM/YYYY
    m_my = re.search(r"\b(\d{1,2})[-/.](\d{4})\b", val_str)
    if m_my:
        year = int(m_my.group(2))
        month = min(12, max(1, int(m_my.group(1))))
        return year, month

    # Check for 4-digit Year only e.g. "2020"
    m_y = re.search(r"\b(19\d{2}|20\d{2})\b", val_str)
    if m_y:
        year = int(m_y.group(1))
        month = 12 if is_end else 1
        return year, month

    return None


def calculate_duration_months(start: Any, end: Any, is_current: bool = False) -> Optional[int]:
    s = parse_date_components(start, is_end=False, is_current=False)
    if not s:
        return None
    e = parse_date_components(end, is_end=True, is_current=is_current)
    if not e:
        return None
    start_idx = s[0] * 12 + s[1]
    end_idx = e[0] * 12 + e[1]
    return max(1, end_idx - start_idx)


def calculate_merged_experience_years(experience_list: List[Dict[str, Any]]) -> float:
    """Calculate total experience years deterministically without double-counting overlapping jobs."""
    intervals: List[Tuple[int, int]] = []
    for item in experience_list or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start_date")
        end = item.get("end_date")
        current = bool(item.get("is_current"))
        s = parse_date_components(start, is_end=False, is_current=False)
        if not s:
            continue
        e = parse_date_components(end, is_end=True, is_current=current)
        if not e:
            continue
        s_idx = s[0] * 12 + s[1]
        e_idx = max(s_idx + 1, e[0] * 12 + e[1])
        intervals.append((s_idx, e_idx))

    if not intervals:
        return 0.0

    intervals.sort(key=lambda x: (x[0], x[1]))
    merged: List[Tuple[int, int]] = []
    curr_start, curr_end = intervals[0]
    for s_idx, e_idx in intervals[1:]:
        if s_idx <= curr_end:
            curr_end = max(curr_end, e_idx)
        else:
            merged.append((curr_start, curr_end))
            curr_start, curr_end = s_idx, e_idx
    merged.append((curr_start, curr_end))

    total_months = sum(end_idx - start_idx for start_idx, end_idx in merged)
    return round(total_months / 12.0, 1)


STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or", "with",
    "by", "from", "as", "is", "was", "are", "were", "be", "been", "that", "this"
}


def clean_str(val: Any) -> str:
    """Normalize string, replace special unicode hyphens/quotes, and collapse whitespace."""
    if val is None:
        return ""
    s = str(val)
    # Replace non-breaking hyphens, en-dashes, em-dashes, minus signs with ASCII '-'
    s = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\uff0d]", "-", s)
    # Replace curved single and double quotes with standard ASCII
    s = re.sub(r"[\u2018\u2019\u201a\u201b]", "'", s)
    s = re.sub(r"[\u201c\u201d\u201e\u201f]", '"', s)
    # Replace non-breaking space
    s = s.replace("\u00a0", " ")
    return " ".join(s.strip().split())


def text_contains_phrase(cv_text: str, phrase: str) -> bool:
    """Anti-hallucination check: verifies that phrase has textual evidence in CV text."""
    if not cv_text or len(cv_text.strip()) < 80:
        # Scanned PDF or image where machine text was unavailable; cannot reject based on text
        return True
    if not phrase or not str(phrase).strip():
        return False
    phrase_clean = clean_str(phrase)
    lowered_text = clean_str(cv_text).casefold()

    # Direct substring check
    if phrase_clean.casefold() in lowered_text:
        return True

    # Check significant tokens
    tokens = [w for w in re.findall(r"[a-zA-Z0-9]+", phrase_clean.casefold()) if w not in STOP_WORDS and len(w) > 2]
    if not tokens:
        return True

    # At least half of significant tokens must appear in the text
    matched = sum(1 for token in tokens if token in lowered_text)
    return (matched / len(tokens)) >= 0.5


def clean_skill_string(val: Any) -> str:
    if isinstance(val, dict):
        val = val.get("skill") or val.get("name") or ""
    return clean_str(val)


def normalize_language_name(raw_name: str) -> str:
    """Normalize language name variants conservatively without changing meaning."""
    if not raw_name:
        return ""
    clean = clean_str(raw_name).strip()
    lowered = clean.casefold()
    if lowered in LANGUAGE_NAME_NORM_MAP:
        return LANGUAGE_NAME_NORM_MAP[lowered]
    cleaned_suffix = re.sub(r"\s+(?:language|spoken)$", "", clean, flags=re.IGNORECASE).strip()
    if cleaned_suffix and cleaned_suffix.casefold() in LANGUAGE_NAME_NORM_MAP:
        return LANGUAGE_NAME_NORM_MAP[cleaned_suffix.casefold()]
    return cleaned_suffix.title() if cleaned_suffix else clean.title()


def resolve_strict_proficiency(lang_name: str, cv_text: str, gemini_level: Optional[str] = None) -> Optional[str]:
    """Return explicit proficiency ONLY if explicitly supported in CV text or Gemini output. Never invent unstated levels."""
    if gemini_level:
        raw_clean = str(gemini_level).strip()
        lowered_raw = raw_clean.lower()
        if lowered_raw not in {lang_name.lower(), "languages", "language", "null", "none", "unknown"}:
            for keyword, label in PROFICIENCY_KEYWORDS.items():
                if keyword in lowered_raw:
                    return label
            if re.match(r"^(?:A1|A2|B1|B2|C1|C2|Native|Fluent|Bilingual|Conversational|Intermediate|Basic|Elementary|Beginner)$", raw_clean, re.I):
                return raw_clean.upper() if len(raw_clean) == 2 else raw_clean.title()

    if not cv_text or not lang_name:
        return None

    lowered_text = cv_text.lower()
    lowered_lang = lang_name.lower()
    matches = [m.start() for m in re.finditer(r"\b" + re.escape(lowered_lang) + r"\b", lowered_text)]
    for pos in matches:
        snippet = lowered_text[max(0, pos - 40): pos + len(lowered_lang) + 40]
        m_cefr = re.search(r"\b(c2|c1|b2|b1|a2|a1)\b", snippet, re.IGNORECASE)
        if m_cefr:
            return m_cefr.group(1).upper()
        for keyword, label in PROFICIENCY_KEYWORDS.items():
            if re.search(r"\b" + re.escape(keyword) + r"\b", snippet):
                return label

    return None


def infer_language_proficiency(lang_name: str, cv_text: str, gemini_level: Optional[str] = None) -> Optional[str]:
    """Backward compatibility wrapper around resolve_strict_proficiency."""
    return resolve_strict_proficiency(lang_name, cv_text, gemini_level)


FALSE_POSITIVE_LANGUAGE_PATTERNS = [
    r"\b(?:ba|ma|b\.a\.|m\.a\.|bachelor|degree|master|diploma|course)\s+(?:of|in)?\s*{lang}\s+(?:literature|linguistics|studies|department|language\s+and\s+literature)\b",
    r"\b{lang}\s+(?:literature|department|client|clients|company|subsidiary|market|branch|office|vendor|customers|product|team)\b",
    r"\b(?:worked|collaborated|dealt)\s+with\s+{lang}\s+(?:clients|customers|teams|companies|partners)\b",
]


def is_valid_candidate_language(lang_name: str, cv_text: str, raw_item: Any = None) -> bool:
    """Post-processing validation stage for candidate human language."""
    if not lang_name or not str(lang_name).strip():
        return False

    norm_lang = normalize_language_name(lang_name)
    lowered_lang = norm_lang.casefold()

    # Rule 1: Programming languages are NEVER human languages
    if lowered_lang in COMMON_PROGRAMMING_LANGUAGES:
        return False

    is_explicit_structure = False
    if isinstance(raw_item, dict) and (raw_item.get("speaking_level") or raw_item.get("level") or raw_item.get("reading_level")):
        is_explicit_structure = True

    if not cv_text or len(cv_text.strip()) < 80:
        return lowered_lang in COMMON_HUMAN_LANGUAGES or is_explicit_structure

    lowered_text = cv_text.casefold()

    # Must appear in cv_text (anti-hallucination & no-inference rule)
    if lowered_lang not in lowered_text:
        aliases = [k for k, v in LANGUAGE_NAME_NORM_MAP.items() if v.casefold() == lowered_lang]
        if not any(alias in lowered_text for alias in aliases):
            return False

    # Check if it appears under an explicit language section or header
    lang_section_match = re.search(
        r"(?:languages?|language\s+(?:skills|proficiency|known)|spoken\s+languages?|communication\s+languages?)\s*[:\-\|\n][^\n\r]*\b" + re.escape(lowered_lang) + r"\b",
        lowered_text,
        re.IGNORECASE
    )
    if lang_section_match or is_explicit_structure:
        return True

    # Check for direct language proficiency patterns e.g. "English - Fluent", "German B2"
    proficiency_pattern = r"\b" + re.escape(lowered_lang) + r"\s*(?:[\-:]|\(|\s+is\s+)\s*(?:native|mother\s+tongue|bilingual|fluent|professional|working|conversational|intermediate|basic|a1|a2|b1|b2|c1|c2|beginner|elementary)\b"
    if re.search(proficiency_pattern, lowered_text, re.IGNORECASE):
        return True

    # Check if it appears ONLY in false-positive contexts (e.g. "BA English Literature", "French company", "German client")
    is_false_positive = False
    for pat_template in FALSE_POSITIVE_LANGUAGE_PATTERNS:
        pat = pat_template.format(lang=re.escape(lowered_lang))
        if re.search(pat, lowered_text, re.IGNORECASE):
            is_false_positive = True
            break

    if is_false_positive:
        return False

    if lowered_lang in COMMON_HUMAN_LANGUAGES:
        if re.search(r"\b(?:languages?|skills|personal|additional|competencies)\b[\s\S]{0,300}\b" + re.escape(lowered_lang) + r"\b", lowered_text, re.IGNORECASE):
            return True
        if re.search(r"\b[A-Z][a-z]+\s*[,\|/]\s*" + re.escape(norm_lang) + r"\b|\b" + re.escape(norm_lang) + r"\s*[,\|/]\s*[A-Z][a-z]+\b", cv_text):
            return True

    return False


def normalize_cv_extraction(raw_payload: Any, cv_text: str = "") -> Dict[str, Any]:
    """Strict schema validation, deterministic deduplication, date normalization, and anti-hallucination filtering."""
    if not isinstance(raw_payload, dict):
        raise ValueError("Gemini response is not a valid JSON object.")

    # 1. Personal Info
    raw_personal = raw_payload.get("personal_info") or {}
    if not isinstance(raw_personal, dict):
        raw_personal = {}

    full_name = clean_str(raw_personal.get("full_name")) or None
    # Anti-hallucination for full name: do not use email addresses or random numbers
    if full_name:
        if "@" in full_name:
            full_name = None
        elif re.search(r"\d{3,}$", full_name):
            # Strip trailing email user numbers (e.g. "thanujan88") unless CV text has them
            base_name = re.sub(r"\d+$", "", full_name).strip()
            if base_name and (not cv_text or base_name.casefold() in cv_text.casefold()):
                full_name = base_name

    phone = clean_str(raw_personal.get("phone")) or None
    location = clean_str(raw_personal.get("location")) or None
    headline = clean_str(raw_personal.get("headline") or raw_personal.get("professional_title")) or None

    social_links_in = raw_payload.get("social_links") or {}
    if not isinstance(social_links_in, dict):
        social_links_in = {}

    linkedin_url = normalize_url(raw_personal.get("linkedin_url") or social_links_in.get("linkedin_url"))
    github_url = normalize_url(social_links_in.get("github_url") or (raw_payload.get("github") or {}).get("profile_url"))
    portfolio_url = normalize_url(raw_personal.get("portfolio_url") or social_links_in.get("portfolio_url"))

    # 2. Languages: Spoken vs Programming
    raw_prog_langs = raw_payload.get("programming_languages") or []
    if not isinstance(raw_prog_langs, list):
        raw_prog_langs = []

    raw_human_langs = raw_payload.get("human_languages") or raw_payload.get("languages") or []
    if not isinstance(raw_human_langs, list):
        raw_human_langs = []

    clean_prog_langs: List[str] = []
    seen_prog: Set[str] = set()
    for item in raw_prog_langs:
        val = clean_skill_string(item)
        if val and val.casefold() not in seen_prog:
            # Check if this was accidentally a human language
            if val.casefold() in COMMON_HUMAN_LANGUAGES:
                raw_human_langs.append({"language": val})
                continue
            seen_prog.add(val.casefold())
            clean_prog_langs.append(val)

    clean_human_langs: List[Dict[str, Any]] = []
    seen_human: Dict[str, Dict[str, Any]] = {}
    for item in raw_human_langs:
        if isinstance(item, str):
            lang_name_raw = clean_skill_string(item)
            speaking = reading = writing = None
        elif isinstance(item, dict):
            lang_name_raw = clean_skill_string(item.get("language") or item.get("name"))
            speaking = item.get("speaking_level") or item.get("level")
            reading = item.get("reading_level") or item.get("level")
            writing = item.get("writing_level") or item.get("level")
        else:
            continue

        if not lang_name_raw or lang_name_raw.lower() in {"languages", "language", "spoken languages", "language skills", "languages known"}:
            continue

        norm_name = normalize_language_name(lang_name_raw)

        # Rule 1: Programming languages are NEVER human languages
        if norm_name.casefold() in COMMON_PROGRAMMING_LANGUAGES or lang_name_raw.casefold() in COMMON_PROGRAMMING_LANGUAGES:
            if norm_name.casefold() not in seen_prog:
                seen_prog.add(norm_name.casefold())
                clean_prog_langs.append(norm_name)
            continue

        # Rule 2: Post-Processing Validation Stage (context validation, false positive rejection, anti-hallucination)
        if not is_valid_candidate_language(norm_name, cv_text, item):
            continue

        # Resolve strict proficiency without hallucinating unstated levels
        raw_level_hint = speaking or reading or writing
        resolved_speaking = resolve_strict_proficiency(norm_name, cv_text, speaking or raw_level_hint)
        resolved_reading = resolve_strict_proficiency(norm_name, cv_text, reading or raw_level_hint)
        resolved_writing = resolve_strict_proficiency(norm_name, cv_text, writing or raw_level_hint)

        key = norm_name.casefold()
        if key not in seen_human:
            entry = {
                "language": norm_name,
                "speaking_level": resolved_speaking,
                "reading_level": resolved_reading,
                "writing_level": resolved_writing,
            }
            seen_human[key] = entry
            clean_human_langs.append(entry)
        else:
            # Deduplication & Merging
            existing = seen_human[key]
            if resolved_speaking and not existing["speaking_level"]:
                existing["speaking_level"] = resolved_speaking
            if resolved_reading and not existing["reading_level"]:
                existing["reading_level"] = resolved_reading
            if resolved_writing and not existing["writing_level"]:
                existing["writing_level"] = resolved_writing


    # 3. Work Experience
    raw_exp = raw_payload.get("experience") or []
    if not isinstance(raw_exp, list):
        raw_exp = []

    clean_exp: List[Dict[str, Any]] = []
    for item in raw_exp:
        if not isinstance(item, dict):
            continue
        company = clean_str(item.get("company"))
        role = clean_str(item.get("role") or item.get("position"))

        # Discard empty cards
        if not company and not role:
            continue

        # Anti-hallucination: at least company or role must have evidence in CV text
        if cv_text and len(cv_text.strip()) >= 100:
            has_company = text_contains_phrase(cv_text, company) if company else False
            has_role = text_contains_phrase(cv_text, role) if role else False
            if not has_company and not has_role:
                continue

        start_date = item.get("start_date")
        end_date = item.get("end_date")
        is_current = bool(item.get("is_current"))
        if not is_current and end_date and str(end_date).strip().lower() in {"present", "current", "now", "ongoing"}:
            is_current = True
            end_date = None

        s_comp = parse_date_components(start_date, is_end=False, is_current=False)
        e_comp = parse_date_components(end_date, is_end=True, is_current=is_current)

        norm_start = f"{s_comp[0]}-{s_comp[1]:02d}" if s_comp else (clean_str(start_date) if start_date else None)
        norm_end = None if is_current else (f"{e_comp[0]}-{e_comp[1]:02d}" if e_comp else (clean_str(end_date) if end_date else None))

        dur_months = calculate_duration_months(start_date, end_date, is_current=is_current)

        skills_used_raw = item.get("skills_used") or item.get("technologies") or []
        skills_used = [clean_skill_string(s) for s in skills_used_raw if clean_skill_string(s)]

        resp_raw = item.get("responsibilities") or []
        if isinstance(resp_raw, str):
            resp_raw = [resp_raw]
        responsibilities = [clean_str(r) for r in resp_raw if clean_str(r)]

        ach_raw = item.get("achievements") or []
        if isinstance(ach_raw, str):
            ach_raw = [ach_raw]
        achievements = [clean_str(a) for a in ach_raw if clean_str(a)]

        clean_exp.append({
            "company": company,
            "role": role,
            "position": role,
            "start_date": norm_start,
            "end_date": norm_end,
            "is_current": is_current,
            "duration_months": dur_months,
            "description": clean_str(item.get("description")),
            "responsibilities": responsibilities,
            "skills_used": skills_used,
            "technologies": skills_used,
            "achievements": achievements,
        })

    # Compute deterministic experience years
    calculated_exp_years = calculate_merged_experience_years(clean_exp)

    # 4. Education
    raw_edu = raw_payload.get("education") or []
    if not isinstance(raw_edu, list):
        raw_edu = []

    clean_edu: List[Dict[str, Any]] = []
    seen_edu: Set[str] = set()
    for item in raw_edu:
        if not isinstance(item, dict):
            continue
        qual = clean_str(item.get("qualification") or item.get("degree"))
        inst = clean_str(item.get("institution"))
        field = clean_str(item.get("field"))

        # Discard empty cards
        if not qual and not inst:
            continue

        # Anti-hallucination check
        if cv_text and len(cv_text.strip()) >= 100:
            has_qual = text_contains_phrase(cv_text, qual) if qual else False
            has_inst = text_contains_phrase(cv_text, inst) if inst else False
            if not has_qual and not has_inst:
                continue

        edu_key = f"{qual.casefold()}|{inst.casefold()}"
        if edu_key in seen_edu:
            continue
        seen_edu.add(edu_key)

        start_year = item.get("start_year")
        end_year = item.get("end_year")
        try:
            start_year = int(start_year) if start_year and 1900 <= int(start_year) <= 2200 else None
        except (ValueError, TypeError):
            start_year = None
        try:
            end_year = int(end_year) if end_year and 1900 <= int(end_year) <= 2200 else None
        except (ValueError, TypeError):
            end_year = None

        clean_edu.append({
            "qualification": qual,
            "institution": inst,
            "field": field,
            "start_year": start_year,
            "end_year": end_year,
            "grade": clean_str(item.get("grade")) or None,
            "degree": qual,
        })

    # 5. Certifications
    raw_certs = raw_payload.get("certifications") or []
    if not isinstance(raw_certs, list):
        raw_certs = []

    clean_certs: List[Dict[str, Any]] = []
    demoted_to_skills: List[str] = []
    seen_certs: Set[str] = set()

    for item in raw_certs:
        if not isinstance(item, dict):
            continue
        name = clean_str(item.get("name"))
        issuer = clean_str(item.get("issuer")) or None
        cred_url = normalize_url(item.get("credential_url"))

        if not name or name.lower() in {"certifications", "certification", "licenses", "license", "certificates"}:
            continue

        name_lower = name.lower()
        has_cert_keyword = any(kw in name_lower for kw in CERTIFICATION_KEYWORDS)

        # STRICT RULE: A skill/topic (e.g. CNN, GAN, PyTorch, Docker, Python) is NOT a certification
        # unless explicitly supported as a course/credential with cert keywords or an issuer/credential URL
        if not has_cert_keyword and not issuer and not cred_url:
            # Check if name is simply a technical term
            demoted_to_skills.append(name)
            continue

        # Anti-hallucination check
        if cv_text and len(cv_text.strip()) >= 100:
            if not text_contains_phrase(cv_text, name):
                continue

        cert_key = f"{name.casefold()}|{(issuer or '').casefold()}"
        if cert_key in seen_certs:
            continue
        seen_certs.add(cert_key)

        skills_raw = item.get("relevant_skills") or item.get("skills") or []
        relevant_skills = [clean_skill_string(s) for s in skills_raw if clean_skill_string(s)]

        clean_certs.append({
            "name": name,
            "issuer": issuer,
            "description": clean_str(item.get("description")),
            "relevant_skills": relevant_skills,
            "issue_date": clean_str(item.get("issue_date")) or None,
            "expiry_date": clean_str(item.get("expiry_date")) or None,
            "credential_id": clean_str(item.get("credential_id")) or None,
            "credential_url": cred_url,
        })

    # 6. Projects
    raw_projects = raw_payload.get("projects") or []
    if not isinstance(raw_projects, list):
        raw_projects = []

    clean_projects: List[Dict[str, Any]] = []
    seen_proj: Set[str] = set()
    for item in raw_projects:
        if not isinstance(item, dict):
            continue
        name = clean_str(item.get("name"))
        if not name or name.lower() in {"projects", "project", "selected projects", "academic projects"}:
            continue

        # Anti-hallucination check
        if cv_text and len(cv_text.strip()) >= 100:
            if not text_contains_phrase(cv_text, name):
                continue

        proj_key = name.casefold()
        if proj_key in seen_proj:
            continue
        seen_proj.add(proj_key)

        technologies = [clean_skill_string(t) for t in (item.get("technologies") or []) if clean_skill_string(t)]
        proj_soft_raw = item.get("soft_skills") or []
        if isinstance(proj_soft_raw, str):
            proj_soft_raw = [proj_soft_raw]
        proj_soft = [clean_skill_string(s) for s in proj_soft_raw if clean_skill_string(s)]

        ach_raw = item.get("achievements") or []
        if isinstance(ach_raw, str):
            ach_raw = [ach_raw]
        achievements = [clean_str(a) for a in ach_raw if clean_str(a)]

        clean_projects.append({
            "name": name,
            "description": clean_str(item.get("description")),
            "technologies": technologies,
            "soft_skills": proj_soft,
            "github_url": normalize_url(item.get("github_url")),
            "demo_url": normalize_url(item.get("url") or item.get("demo_url")),
            "achievements": achievements,
        })

    # 7. Skills Consolidation, Evidence Harvesting & Canonical Normalization
    raw_tech = raw_payload.get("technical_skills") or []
    if not isinstance(raw_tech, list):
        raw_tech = []

    raw_soft = raw_payload.get("soft_skills") or []
    if not isinstance(raw_soft, list):
        raw_soft = []

    # Assemble full text corpus from all sections for evidence harvesting
    corpus_parts = [cv_text or ""]
    if raw_payload.get("professional_summary"):
        corpus_parts.append(str(raw_payload["professional_summary"]))
    for p in clean_projects:
        corpus_parts.extend([p.get("name", ""), p.get("description", ""), *p.get("achievements", [])])
    for e in clean_exp:
        corpus_parts.extend([e.get("company", ""), e.get("role", ""), e.get("description", ""), *e.get("responsibilities", []), *e.get("achievements", [])])
    for c in clean_certs:
        corpus_parts.extend([c.get("name", ""), c.get("description", "")])
    full_corpus = " ".join(corpus_parts)
    full_corpus_lower = full_corpus.casefold()

    # Collect raw candidate skills from direct lists, projects, experience, certs (excluding cert providers)
    candidate_skill_items: List[Tuple[Any, str]] = []
    for item in raw_tech:
        candidate_skill_items.append((item, "skills"))
    for item in demoted_to_skills:
        candidate_skill_items.append((item, "certs"))
    for p in clean_projects:
        for item in p.get("technologies") or []:
            candidate_skill_items.append((item, "projects"))
    for e in clean_exp:
        for item in e.get("skills_used") or []:
            candidate_skill_items.append((item, "experience"))
    for c in clean_certs:
        for item in c.get("relevant_skills") or []:
            item_clean = clean_skill_string(item).casefold()
            if item_clean not in CERTIFICATION_PROVIDERS:
                candidate_skill_items.append((item, "certs"))

    # Also scan text corpus for known skills/competencies in SKILL_ALIAS_MAP
    for term, canonical_name in SKILL_ALIAS_MAP.items():
        if len(term) <= 2 and term not in {"r", "c", "go", "ai", "ml"}:
            continue
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, full_corpus_lower):
            candidate_skill_items.append((canonical_name, "corpus"))

    # Scan behavioral action patterns for soft skills / competencies
    behavioral_patterns = [
        (r"\b(?:led|managed|headed|supervised)\s+(?:\d+|five|several|a team|annotators|engineers|developers)\b", ["Leadership", "Team Management"]),
        (r"\b(?:team lead|lead developer|lead engineer|tech lead|technical lead)\b", ["Leadership", "Team Management"]),
        (r"\b(?:collaborated|worked jointly|cross-functional|partnered)\b", ["Collaboration", "Cross-functional Collaboration"]),
        (r"\b(?:pre-sales|client facing|presented to clients|worked with clients|client communication)\b", ["Client Communication", "Pre-sales"]),
        (r"\b(?:stakeholder|aligned product|business requirements)\b", ["Stakeholder Alignment", "Communication"]),
        (r"\b(?:translated business requirements|requirements analysis|requirements translation)\b", ["Requirements Analysis", "Communication"]),
    ]
    for pattern, soft_list in behavioral_patterns:
        if re.search(pattern, full_corpus_lower):
            for soft_item in soft_list:
                candidate_skill_items.append((soft_item, "behavioral_evidence"))

    # Process and deduplicate all gathered skills
    seen_tech: Set[str] = set()
    clean_tech: List[Dict[str, Any]] = []

    seen_soft: Set[str] = set()
    clean_soft: List[Dict[str, Any]] = []

    # First process soft skills from JSON & projects
    raw_soft_items = [*raw_soft]
    for p in clean_projects:
        raw_soft_items.extend(p.get("soft_skills") or [])

    for item in raw_soft_items:
        val = canonicalize_skill_name(item)
        if not val:
            continue
        val_lower = val.casefold()
        if not is_soft_skill_candidate(val):
            if val_lower not in seen_tech and val_lower not in COMMON_PROGRAMMING_LANGUAGES:
                seen_tech.add(val_lower)
                clean_tech.append({"skill": val, "confidence": 0.85})
            continue
        if val_lower not in seen_soft:
            seen_soft.add(val_lower)
            clean_soft.append({"skill": val, "confidence": 0.85})

    # Process all candidate technical / domain / discovered skills
    for item, source in candidate_skill_items:
        val = canonicalize_skill_name(item)
        if not val:
            continue
        val_lower = val.casefold()

        # Check programming languages
        if val_lower in COMMON_PROGRAMMING_LANGUAGES:
            if val_lower not in seen_prog:
                seen_prog.add(val_lower)
                clean_prog_langs.append(val)
            continue

        # Soft skill vs technical skill routing
        if is_soft_skill_candidate(val):
            if val_lower not in seen_soft:
                seen_soft.add(val_lower)
                clean_soft.append({"skill": val, "confidence": 0.85 if source != "corpus" else 0.8})
        else:
            if val_lower not in seen_tech:
                # Anti-hallucination check: must have evidence if CV text is available
                if cv_text and len(cv_text.strip()) >= 80 and source == "skills":
                    if not text_contains_phrase(full_corpus, val) and val_lower not in SKILL_ALIAS_MAP:
                        continue
                seen_tech.add(val_lower)
                clean_tech.append({"skill": val, "confidence": 0.9 if source in {"skills", "projects", "certs"} else 0.85})

    # Fallback soft skill extraction from text if soft skills count is low
    if len(clean_soft) < 2:
        COMMON_SOFT_PATTERNS = {
            "leadership": "Leadership", "leader": "Leadership",
            "communication": "Communication", "communicat": "Communication",
            "teamwork": "Teamwork", "collaboration": "Collaboration", "collaborat": "Collaboration",
            "problem solving": "Problem Solving", "problem-solving": "Problem Solving",
            "time management": "Time Management", "critical thinking": "Critical Thinking",
            "adaptability": "Adaptability", "negotiation": "Negotiation",
            "conflict resolution": "Conflict Resolution", "attention to detail": "Attention to Detail",
        }
        for pattern, soft_name in COMMON_SOFT_PATTERNS.items():
            if pattern in full_corpus_lower and soft_name.casefold() not in seen_soft:
                if is_soft_skill_candidate(soft_name):
                    seen_soft.add(soft_name.casefold())
                    clean_soft.append({"skill": soft_name, "confidence": 0.75})

    # Final validated structure matching domain models
    return {
        "personal_info": {
            "full_name": full_name,
            "email": str(raw_personal.get("email") or "").strip() or None,
            "phone": phone,
            "location": location,
            "professional_title": headline,
            "headline": headline,
            "bio": clean_str(raw_payload.get("professional_summary") or raw_personal.get("bio")) or None,
            "experience_years": calculated_exp_years,
            "linkedin_url": linkedin_url,
            "portfolio_url": portfolio_url,
        },
        "professional_summary": clean_str(raw_payload.get("professional_summary")),
        "programming_languages": clean_prog_langs,
        "human_languages": clean_human_langs,
        "languages": clean_human_langs,
        "technical_skills": clean_tech,
        "soft_skills": clean_soft,
        "experience": clean_exp,
        "projects": clean_projects,
        "education": clean_edu,
        "certifications": clean_certs,
        "github": {
            "profile_url": github_url,
            "username": None,
            "repositories": [],
        },
        "social_links": {
            "linkedin_url": linkedin_url,
            "github_url": github_url,
            "portfolio_url": portfolio_url,
        },
    }

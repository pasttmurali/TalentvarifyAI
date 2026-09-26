# =========================================================================================
# FILE: cv_analysis_service.py
# PURPOSE: Core candidate intelligence service that orchestrates AI extraction via Gemini,
#          validates entities against strict domain models, fetches GitHub evidence,
#          and calculates the 9-dimensional candidate evaluation score.
# =========================================================================================

"""CV Extraction and Complete Candidate Intelligence Analysis Service."""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cv_document import find_github_profile_url
from domain import duration_months
from gemini_rest import generate_json as generate_gemini_json
from repositories.candidate_repository import CandidateRepository
from repositories.analysis_repository import AnalysisRepository
from repositories.github_repository import GithubRepository
from services.github_service import GithubService
from services.scoring_service import ScoringService


def utcnow():
    return datetime.now(timezone.utc)


def identifier():
    return str(uuid.uuid4())


COMMON_PROGRAMMING_LANGUAGES = {
    "python", "java", "javascript", "typescript", "c", "c++", "c#", "go", "golang", "rust",
    "swift", "kotlin", "php", "ruby", "sql", "html", "css", "bash", "shell", "r", "scala",
    "dart", "perl", "haskell", "lua", "matlab", "powershell", "assembly"
}


def _skill_names(items):
    names = []
    seen = set()
    for item in items or []:
        value = item.get("skill") if isinstance(item, dict) else item
        label = " ".join(str(value or "").split())
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            names.append(label)
    return names


from cv_normalization import (
    COMMON_PROGRAMMING_LANGUAGES,
    canonicalize_skill_name,
    clean_skill_string,
    is_soft_skill_candidate,
    normalize_cv_extraction,
)


# -----------------------------------------------------------------------------------------
# STEP 1: CV STRUCTURE NORMALIZATION HELPER
# WHY THIS STEP:
# - Separates technical programming languages from soft skills and general competencies.
# - Calculates employment duration in months for every job listed on the CV.
# - Maps social links (GitHub, LinkedIn, personal portfolio) to canonical profile fields.
# -----------------------------------------------------------------------------------------
def _analysis_shape(structured):
    """Convert the validated CvExtraction payload into the intelligence-service shape."""
    personal = dict(structured.get("personal_info") or {})
    if personal.get("professional_title") and not personal.get("headline"):
        personal["headline"] = personal["professional_title"]

    technical = _skill_names(structured.get("technical_skills"))
    programming = [skill for skill in technical if skill.casefold() in COMMON_PROGRAMMING_LANGUAGES]
    for p in (structured.get("programming_languages") or []):
        p_name = clean_skill_string(p)
        if p_name and p_name.casefold() not in {x.casefold() for x in programming}:
            programming.append(p_name)
    technical = [skill for skill in technical if skill.casefold() not in COMMON_PROGRAMMING_LANGUAGES]
    experiences = []
    for source in structured.get("experience") or []:
        item = dict(source)
        item["role"] = item.get("role") or item.get("position")
        item["skills_used"] = item.get("skills_used") or item.get("technologies") or []
        item["duration_months"] = item.get("duration_months") or duration_months(
            item.get("start_date"), item.get("end_date"), item.get("is_current", False)
        )
        experiences.append(item)

    github = structured.get("github") or {}

    # Consolidate soft skills from root soft_skills and project soft_skills, enforcing Universal CV Rule
    raw_soft_candidates = _skill_names(structured.get("soft_skills"))
    proj_soft_candidates = [clean_skill_string(s) for proj in (structured.get("projects") or []) for s in (proj.get("soft_skills") or [])]
    soft_list = []
    seen_soft = set()
    for s in [*raw_soft_candidates, *proj_soft_candidates]:
        if s and is_soft_skill_candidate(s) and s.casefold() not in seen_soft:
            seen_soft.add(s.casefold())
            soft_list.append(s)

    return {
        "personal_info": personal,
        "professional_summary": structured.get("professional_summary") or "",
        "programming_languages": programming,
        "human_languages": structured.get("human_languages") or structured.get("languages") or [],
        "technical_skills": technical,
        "soft_skills": soft_list,
        "experience": experiences,
        "projects": structured.get("projects") or [],
        "education": structured.get("education") or [],
        "certifications": structured.get("certifications") or [],
        "social_links": {
            "linkedin_url": personal.get("linkedin_url"),
            "github_url": github.get("profile_url"),
            "portfolio_url": personal.get("portfolio_url"),
        },
    }


class CvAnalysisService:
    def __init__(
        self,
        candidate_repo: CandidateRepository,
        analysis_repo: AnalysisRepository,
        github_service: GithubService,
        api_key: str = "",
        model_name: str = "gemini-3.5-flash-lite"
    ):
        self.candidate_repo = candidate_repo
        self.analysis_repo = analysis_repo
        self.github_service = github_service
        self.scoring_service = ScoringService()
        self.api_key = api_key
        self.model_name = model_name

    # -------------------------------------------------------------------------------------
    # STEP 2: END-TO-END CV ANALYSIS & REPOSITORY PERSISTENCE
    # WHY THIS STEP:
    # - Formats prompt with strict rules preventing hallucination.
    # - Calls Gemini 3.5 Flash Lite with structured JSON output schema.
    # - Validates returned JSON with Pydantic domain models (CvExtraction).
    # - Asynchronously fetches GitHub commit/repo evidence if GitHub username is discovered.
    # - Saves clean Candidate profile to MongoDB candidate_repository.
    # -------------------------------------------------------------------------------------
    async def analyze_and_store_cv(
        self,
        user_id: str,
        cv_text: str,
        file_name: str = "",
        document_id: Optional[str] = None,
        structured_extraction: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Extract, normalize, verify, score, and permanently store candidate profile."""
        doc_id = document_id or identifier()
        
        prompt = f"""Extract the candidate's full name exactly as written and their complete address/location when present.
Extract every project and certification. List all project technologies and certification relevant_skills explicitly
supported by the certification title or description. Also include those technologies and certification skills in
technical_skills. Never invent an address, credential, skill, employer, date, or qualification.
The technical_skills field means hard or functional professional competencies for any industry (e.g. Financial Reporting, Patient Care, Digital Marketing, Inventory Management, Recruitment, Project Management, Data Analysis, AutoCAD, etc.).

UNIVERSAL CV RULE FOR SOFT SKILLS EXTRACTION:
Classify soft_skills (such as Communication, Teamwork, Leadership, Adaptability, Negotiation, Conflict Resolution, Problem Solving, Critical Thinking, Time Management, Attention to Detail) semantically:
- Is it a hard/functional/domain/managerial discipline (e.g. Financial Reporting, Patient Care, Digital Marketing, Inventory Management, Recruitment, Project Management, AutoCAD)? -> Put in technical_skills. Do NOT put in soft_skills.
- Does it describe how the person communicates, collaborates, leads, thinks, adapts, or behaves at work? -> Include in soft_skills.
- When uncertain, exclude from soft_skills rather than incorrectly classifying a domain skill as a soft skill.

Extract complete, factual professional information from this CV. Never invent missing details.
Explicitly separate programming_languages (e.g. Python, Java, JavaScript, TypeScript, SQL, HTML, CSS) from human_languages (e.g. English, Tamil, Sinhala, Spanish, French).

Return valid JSON with no markdown:
{{
    "personal_info": {{
        "full_name": "Name",
        "email": "Email",
        "phone": "Phone",
        "location": "City, Country",
        "headline": "Title",
        "bio": "Professional Summary",
        "experience_years": 3.5
    }},
    "professional_summary": "Factual professional summary",
    "programming_languages": ["Python", "JavaScript", "SQL"],
    "human_languages": [
        {{"language": "English", "speaking_level": "Fluent", "reading_level": "Fluent", "writing_level": "Fluent"}},
        {{"language": "Tamil", "speaking_level": "Fluent", "reading_level": "Moderate", "writing_level": "Moderate"}}
    ],
    "technical_skills": ["FastAPI", "React", "Docker", "MongoDB"],
    "soft_skills": ["Communication", "Problem Solving"],
    "experience": [
        {{
            "company": "Company Name",
            "role": "Role Title",
            "start_date": "YYYY-MM",
            "end_date": "YYYY-MM",
            "is_current": false,
            "duration_months": 24,
            "description": "Role summary",
            "responsibilities": ["Responsibility 1"],
            "skills_used": ["Python", "FastAPI"],
            "achievements": ["Achievement 1"]
        }}
    ],
    "projects": [
        {{
            "name": "Project Name",
            "description": "Project details",
            "technologies": ["FastAPI", "MongoDB"],
            "soft_skills": ["Leadership", "Agile"],
            "github_url": "https://github.com/...",
            "achievements": ["Built REST API"]
        }}
    ],
    "education": [
        {{
            "qualification": "B.Sc in Computer Science",
            "institution": "University Name",
            "field": "Computer Science",
            "start_year": 2018,
            "end_year": 2022,
            "grade": "First Class"
        }}
    ],
    "certifications": [
        {{
            "name": "AWS Certified Developer",
            "issuer": "Amazon Web Services",
            "description": "Topics explicitly described in the CV",
            "relevant_skills": ["AWS", "Cloud Development"],
            "issue_date": "2023",
            "credential_url": "https://..."
        }}
    ],
    "social_links": {{
        "linkedin_url": "https://linkedin.com/in/...",
        "github_url": "https://github.com/...",
        "portfolio_url": "https://..."
    }}
}}

CV TEXT:
{cv_text[:30000]}
"""

        extracted = _analysis_shape(structured_extraction) if structured_extraction else {}
        if not extracted and self.api_key:
            last_exc = None
            for _ in range(2):
                try:
                    candidate_raw = await asyncio.to_thread(generate_gemini_json, self.api_key, self.model_name, prompt)
                    if candidate_raw:
                        extracted = _analysis_shape(normalize_cv_extraction(candidate_raw, cv_text))
                        break
                except Exception as exc:
                    last_exc = exc
            if not extracted:
                if last_exc:
                    raise RuntimeError(f"Gemini CV extraction failed: {last_exc}") from last_exc
                extracted = {}

        # Skills evidenced in projects and certifications belong in the main skills list too.
        project_skills = [skill for project in extracted.get("projects") or []
                          for skill in project.get("technologies") or []]
        project_soft = [skill for project in extracted.get("projects") or []
                        for skill in project.get("soft_skills") or []]
        certification_skills = [skill for certification in extracted.get("certifications") or []
                                for skill in certification.get("relevant_skills") or certification.get("skills") or []]
        combined_technical = []
        seen_technical = set()
        for skill in [*(extracted.get("technical_skills") or []), *project_skills, *certification_skills]:
            label = canonicalize_skill_name(skill) or " ".join(str(skill or "").strip().split())
            key = label.casefold()
            if label and key not in seen_technical:
                seen_technical.add(key)
                combined_technical.append(label)
        extracted["technical_skills"] = combined_technical

        combined_soft = []
        seen_soft_keys = set()
        for skill in [*(extracted.get("soft_skills") or []), *project_soft]:
            label = canonicalize_skill_name(skill) or " ".join(str(skill or "").strip().split())
            key = label.casefold()
            if label and is_soft_skill_candidate(label) and key not in seen_soft_keys:
                seen_soft_keys.add(key)
                combined_soft.append(label)
        extracted["soft_skills"] = combined_soft

        # Fallback / normalization for programming vs human languages
        prog_langs = extracted.get("programming_languages") or []
        human_langs = extracted.get("human_languages") or []
        tech_skills = extracted.get("technical_skills") or []
        soft_skills = extracted.get("soft_skills") or []

        # Auto-separate programming languages if mixed into technical skills
        cleaned_tech = []
        for s in tech_skills:
            if str(s).lower() in COMMON_PROGRAMMING_LANGUAGES and s not in prog_langs:
                prog_langs.append(s)
            else:
                cleaned_tech.append(s)

        # Detect GitHub URL in extraction or CV text
        github_url = (extracted.get("social_links") or {}).get("github_url")
        github_url = github_url or find_github_profile_url(cv_text)

        github_data = None
        if github_url:
            try:
                github_data = await self.github_service.verify_and_store_github(
                    user_id,
                    github_url,
                    cv_skills=prog_langs + cleaned_tech
                )
            except Exception:
                github_data = None

        candidate_data = {
            "id": user_id,
            "user_id": user_id,
            "personal_info": extracted.get("personal_info") or {},
            "professional_summary": extracted.get("professional_summary") or "",
            "programming_languages": prog_langs,
            "human_languages": human_langs,
            "technical_skills": cleaned_tech,
            "soft_skills": soft_skills,
            "experience": extracted.get("experience") or [],
            "projects": extracted.get("projects") or [],
            "education": extracted.get("education") or [],
            "certifications": extracted.get("certifications") or [],
            "social_links": extracted.get("social_links") or {},
        }

        # Calculate Candidate Profile Score (out of 100) deterministically
        profile_score_res = self.scoring_service.calculate_candidate_profile_score(candidate_data, github_data)
        candidate_data["profile_score"] = profile_score_res["overall_score"]
        candidate_data["score_breakdown"] = profile_score_res["score_breakdown"]

        # Update candidate profile in MongoDB without creating duplicate candidates
        saved_profile = self.candidate_repo.save_or_update_profile(user_id, candidate_data)

        # Record immutable analysis_run entry in MongoDB for audit history
        run_id = f"run_{user_id}_{int(utcnow().timestamp())}"
        analysis_run_doc = {
            "id": run_id,
            "candidate_id": user_id,
            "job_id": None,
            "cv_document_id": doc_id,
            "document_version": 1,
            "extracted_data_snapshot": candidate_data,
            "verification_results": {
                "github_verified": bool(github_data),
                "github_url": github_url,
            },
            "scoring_rules_version": "2.5",
            "model_version": self.model_name,
            "candidate_profile_score": profile_score_res["overall_score"],
            "individual_score_components": profile_score_res["score_breakdown"],
            "evidence": {
                "programming_languages": prog_langs,
                "human_languages": human_langs,
                "github": (github_data or {}).get("detected_skills", [])
            },
            "analysis_timestamp": utcnow(),
        }
        self.analysis_repo.record_analysis_run(analysis_run_doc)

        return {
            "candidate": saved_profile,
            "candidate_profile_score": profile_score_res["overall_score"],
            "score_breakdown": profile_score_res["score_breakdown"],
            "github_verification": github_data,
            "analysis_run_id": run_id,
        }

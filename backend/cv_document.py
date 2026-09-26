# =========================================================================================
# FILE: cv_document.py
# PURPOSE: High-accuracy raw text and hyperlink extraction from resumes (PDF, DOCX, TXT).
#          Extracts embedded document links (GitHub, LinkedIn, Portfolio) that visual text
#          alone might miss (e.g. hyperlinks hidden behind anchor words like 'Click here').
# =========================================================================================

"""Text and hyperlink extraction for supported CV document formats."""

import io
import re
from urllib.parse import urlparse

from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader


# -----------------------------------------------------------------------------------------
# STEP 1: REGEX PATTERNS FOR HYPERLINK & SOCIAL MEDIA DISCOVERY
# WHY THIS STEP:
# - Matches raw URL schemas (http/https).
# - Accurately extracts GitHub user profiles (github.com/<username>).
# - Accurately extracts LinkedIn personal profiles (linkedin.com/in/<profile_id>).
# -----------------------------------------------------------------------------------------
URL_PATTERN = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.IGNORECASE)
GITHUB_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com\s*/\s*([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)",
    re.IGNORECASE,
)
LINKEDIN_PATTERN = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com\s*/\s*in\s*/\s*([A-Za-z0-9_%~-]+)",
    re.IGNORECASE,
)


def _clean_url(value):
    value = re.sub(r"\s+", "", str(value or "").strip()).rstrip(".,;:)>}]'\"")
    if not value:
        return None
    if not re.match(r"^https?://", value, re.IGNORECASE):
        if re.match(r"^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(/.*)?$", value):
            value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _unique_urls(values):
    result = []
    seen = set()
    for value in values:
        cleaned = _clean_url(value)
        key = cleaned.casefold() if cleaned else ""
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _pdf_urls(reader):
    urls = []
    for page in reader.pages:
        annotations = page.get("/Annots") or []
        if hasattr(annotations, "get_object"):
            try:
                annotations = annotations.get_object()
            except Exception:
                pass
        for annotation_ref in annotations:
            try:
                annotation = annotation_ref.get_object() if hasattr(annotation_ref, "get_object") else annotation_ref
                if not isinstance(annotation, dict):
                    continue
                action = annotation.get("/A") or {}
                if hasattr(action, "get_object"):
                    action = action.get_object()
                uri = action.get("/URI") if isinstance(action, dict) else None
                if not uri:
                    uri = annotation.get("/URI")
                if uri:
                    urls.append(str(uri))
            except Exception:
                continue
    return urls


def _xml_text(element):
    """Read visible Word text, including runs inside shapes and text boxes."""
    return "".join(node.text or "" for node in element.iter(qn("w:t"))).strip()


def _docx_lines(document):
    """Extract body blocks, table cells, headers, and footers without losing layout sections."""
    lines = []

    def add(value):
        value = " ".join(str(value or "").split())
        if value and (not lines or lines[-1] != value):
            lines.append(value)

    for block in document.element.body.iterchildren():
        if block.tag == qn("w:tbl"):
            for row in block.iterchildren(qn("w:tr")):
                cells = [_xml_text(cell) for cell in row.iterchildren(qn("w:tc"))]
                add(" | ".join(cell for cell in cells if cell))
        else:
            add(_xml_text(block))

    seen_parts = set()
    for section in document.sections:
        for part in (section.header, section.footer):
            part_key = str(part.part.partname)
            if part_key in seen_parts:
                continue
            seen_parts.add(part_key)
            for block in part._element.iterchildren():
                add(_xml_text(block))
    return lines


def _docx_urls(document):
    parts = [document.part]
    for section in document.sections:
        parts.extend((section.header.part, section.footer.part))
    urls = []
    seen_parts = set()
    for part in parts:
        part_key = str(part.partname)
        if part_key in seen_parts:
            continue
        seen_parts.add(part_key)
        urls.extend(rel.target_ref for rel in part.rels.values() if rel.reltype.endswith("/hyperlink"))
    return urls


# -----------------------------------------------------------------------------------------
# STEP 2: MASTER MULTI-FORMAT CV TEXT & LINK EXTRACTION
# WHY THIS STEP:
# - PDF: Reads text page-by-page with PyPDF and extracts hidden hyperlink annotations (/Annots).
# - DOCX: Parses paragraph text and table cell contents with python-docx, including XML relations.
# - TXT: Decodes UTF-8 text with BOM handling.
# - Images: Text is left empty to be routed to Gemini Vision OCR in subsequent pipeline steps.
# - Deduplicates discovered URLs and appends them to text for downstream AI ingestion.
# -----------------------------------------------------------------------------------------
def extract_cv_content(content, extension):
    """Return readable text plus URLs stored as document hyperlinks."""
    if extension == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        links = _pdf_urls(reader)
    elif extension == ".docx":
        document = Document(io.BytesIO(content))
        text = "\n".join(_docx_lines(document))
        links = _docx_urls(document)
    elif extension == ".txt":
        text = content.decode("utf-8-sig")
        links = []
    elif extension in {".jpg", ".jpeg", ".png", ".webp"}:
        text = ""
        links = []
    else:
        raise ValueError("Unsupported CV format")

    links = _unique_urls([*links, *URL_PATTERN.findall(text)])
    if links:
        text = f"{text.rstrip()}\n\nDocument links:\n" + "\n".join(links)
    return text.strip(), links


def find_github_profile_url(text, links=()):
    """Find and normalize a GitHub profile even when PDF text spaces the URL."""
    for value in [*links, text]:
        match = GITHUB_PATTERN.search(str(value or ""))
        if match:
            return f"https://github.com/{match.group(1)}"
    return None


def find_linkedin_profile_url(text, links=()):
    """Find a public member URL in visible CV text or embedded hyperlinks."""
    for value in [*links, text]:
        match = LINKEDIN_PATTERN.search(str(value or ""))
        if match:
            slug = match.group(1).rstrip(".,;:")
            return f"https://www.linkedin.com/in/{slug}"
    return None


def infer_cv_sections_from_text(text):
    """Best-effort fallback extraction for common plain-text CV sections when Gemini returns empty arrays."""
    if not text or not isinstance(text, str):
        return {"languages": [], "projects": [], "certifications": [], "education": []}

    lowered = text.casefold()
    languages = []
    projects = []
    certifications = []
    education = []

    language_names = {
        "english": "English", "sinhala": "Sinhala", "sinhalese": "Sinhala", "tamil": "Tamil",
        "french": "French", "spanish": "Spanish", "german": "German", "japanese": "Japanese",
        "mandarin": "Mandarin", "chinese": "Chinese", "cantonese": "Cantonese",
        "hindi": "Hindi", "arabic": "Arabic", "korean": "Korean", "italian": "Italian",
        "portuguese": "Portuguese", "russian": "Russian", "dutch": "Dutch",
        "bengali": "Bengali", "urdu": "Urdu", "malay": "Malay", "swedish": "Swedish",
        "norwegian": "Norwegian", "danish": "Danish", "finnish": "Finnish", "greek": "Greek",
        "turkish": "Turkish", "polish": "Polish", "czech": "Czech", "hungarian": "Hungarian",
        "romanian": "Romanian", "ukrainian": "Ukrainian", "hebrew": "Hebrew",
        "thai": "Thai", "vietnamese": "Vietnamese", "indonesian": "Indonesian",
        "tagalog": "Tagalog", "persian": "Persian", "farsi": "Farsi", "swahili": "Swahili",
        "punjabi": "Punjabi", "gujarati": "Gujarati", "marathi": "Marathi",
        "telugu": "Telugu", "kannada": "Kannada", "malayalam": "Malayalam",
        "nepali": "Nepali", "burmese": "Burmese", "afrikaans": "Afrikaans",
        "catalan": "Catalan", "serbian": "Serbian", "croatian": "Croatian",
        "slovak": "Slovak", "bulgarian": "Bulgarian", "slovenian": "Slovenian",
        "latvian": "Latvian", "lithuanian": "Lithuanian", "estonian": "Estonian",
        "albanian": "Albanian", "welsh": "Welsh", "irish": "Irish", "maltese": "Maltese",
    }

    proficiency_map = {
        "native": "Native", "mother tongue": "Native", "first language": "Native",
        "fluent": "Fluent", "professional": "Fluent", "full professional": "Fluent",
        "advanced": "Advanced", "upper intermediate": "Advanced",
        "intermediate": "Intermediate", "moderate": "Intermediate",
        "conversational": "Conversational",
        "basic": "Basic", "beginner": "Basic", "elementary": "Basic",
        "limited": "Basic",
    }

    def get_proficiency(context_str):
        ctx = (context_str or "").lower()
        for kw, label in proficiency_map.items():
            if kw in ctx:
                return label
        return None

    language_pattern = re.compile(r"(?:languages?|known languages?|spoken languages?)\s*[:\-]\s*(.+)", re.IGNORECASE)
    match = language_pattern.search(text)
    if match:
        candidates = [part.strip() for part in re.split(r"[,;]|\n+", match.group(1)) if part.strip()]
        seen = set()
        for candidate in candidates:
            cleaned = candidate.strip().rstrip(".-: ")
            cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned)
            cleaned = cleaned.replace("–", "-")
            for key, label in language_names.items():
                if cleaned.casefold().startswith(key) or f" {key}" in cleaned.casefold() or cleaned.casefold() == key:
                    if label not in seen:
                        seen.add(label)
                        level = get_proficiency(cleaned)
                        languages.append({"language": label, "speaking_level": level, "reading_level": level, "writing_level": level})
                    break
            else:
                if cleaned and cleaned not in seen and len(cleaned.split()) <= 3:
                    seen.add(cleaned)
                    level = get_proficiency(cleaned)
                    languages.append({"language": cleaned, "speaking_level": level, "reading_level": level, "writing_level": level})


    certification_pattern = re.compile(r"(?:certifications?|licenses?|training)\s*[:\-]?\s*(.*)", re.IGNORECASE | re.DOTALL)
    cert_match = certification_pattern.search(text)
    if cert_match:
        block = cert_match.group(1)
        items = [part.strip() for part in re.split(r"\n\s*\d+[\.)]|\n\s*[-•*]\s*", block) if part.strip()]
        for item in items[:10]:
            title = item.split(" - ", 1)[0].split(" | ", 1)[0].strip()
            issuer = ""
            if " - " in item:
                issuer = item.split(" - ", 1)[1].strip()
            elif " | " in item:
                issuer = item.split(" | ", 1)[1].strip()
            if title and title.lower() not in {"certifications", "certification", "license", "training"}:
                certifications.append({"name": title, "issuer": issuer, "description": item, "relevant_skills": [], "confidence": 0.5})

    education_pattern = re.compile(r"(?:education|academic background|qualification)\s*[:\-]?\s*(.*)", re.IGNORECASE | re.DOTALL)
    edu_match = education_pattern.search(text)
    if edu_match:
        block = edu_match.group(1)
        entries = [part.strip() for part in re.split(r"\n\s*\d+[\.)]|\n\s*[-•*]\s*", block) if part.strip()]
        for entry in entries[:10]:
            if len(entry) < 8:
                continue
            q = entry.split(" - ", 1)[0].split(" | ", 1)[0].strip()
            if re.search(r"\b(?:b\.sc|bsc|b\.a|ba|m\.sc|msc|mba|phd|diploma|certificate|degree|higher diploma)\b", q, re.I):
                education.append({"qualification": q, "institution": "", "field": "", "start_year": None, "end_year": None, "grade": "", "degree": q})

    project_pattern = re.compile(r"(?:projects?|project highlights?)\s*[:\-]?\s*(.*)", re.IGNORECASE | re.DOTALL)
    project_match = project_pattern.search(text)
    if project_match:
        block = project_match.group(1)
        chunks = [part.strip() for part in re.split(r"\n\s*\d+[\.)]|\n\s*[-•*]\s*", block) if part.strip()]
        for chunk in chunks[:10]:
            if len(chunk) < 6:
                continue
            cleaned = re.sub(r"^\d+[\.)\s-]*", "", chunk).strip()
            if not cleaned or cleaned.lower() in {"projects", "project"}:
                continue

            title = cleaned
            description = cleaned
            for separator in (" — ", " - ", " – ", " : "):
                if separator in cleaned:
                    left, right = cleaned.split(separator, 1)
                    left = left.strip()
                    if left and len(left) <= 120:
                        title = left
                        description = right.strip() if right.strip() else cleaned
                        break

            title = re.sub(r"\s+", " ", title).strip().rstrip(".-: ")
            if title and title.lower() not in {"projects", "project"}:
                projects.append({"name": title, "description": description, "technologies": [], "soft_skills": []})
    return {"languages": languages[:10], "projects": projects[:10], "certifications": certifications[:10], "education": education[:10]}


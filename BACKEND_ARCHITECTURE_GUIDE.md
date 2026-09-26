# ⚙️ TalentVerifyAI - Backend & AI Engine Architecture Documentation

This guide provides a complete technical explanation of the **Backend REST API Layer** and **AI Verification Engine** of TalentVerifyAI, built using FastAPI, Python 3.14, and Google Gemini AI.

---

## 🏗️ 1. Overview & Technology Stack

The TalentVerifyAI backend serves high-performance REST endpoints, executes automated candidate CV extractions, performs GitHub code audits, and calculates authoritative multi-category job match scores.

- **Web Framework**: FastAPI (Asynchronous Python Web Framework)
- **ASGI Server**: Uvicorn
- **AI Model**: Google Gemini API (`gemini-3.5-flash-lite` / REST)
- **Middleware**: FastAPI `CORSMiddleware` + `GZipMiddleware` (75% payload compression)
- **Security**: PBKDF2-HMAC-SHA256 Password Hashing + Cryptographic Token Auth

---

## 📂 2. File & Service Directory

```text
backend/
├── main.py                             # Primary FastAPI app entry, middleware & route definitions
├── database.py                         # MongoDB connection pool initialization
├── domain.py                           # Domain models, scoring weights & manual profile update logic
├── github_evidence.py                  # Public GitHub REST API profile & repository scanner
├── cv_document.py                      # CV text extraction (PDF, DOCX, TXT) helper
├── cv_normalization.py                 # Gemini JSON schema definitions for skill extractions
├── gemini_rest.py                      # Direct HTTP client for Gemini REST API calls
├── assessment_routes.py                # Specialized routes for skills matrix & evaluations
└── services/
    ├── candidate_service.py            # Complete candidate intelligence profile aggregator
    ├── cv_analysis_service.py          # Complete CV parsing & profile mapping pipeline
    ├── scoring_service.py              # Candidate Profile Score calculator (out of 100)
    ├── job_match_service.py            # Job Match Score algorithm calculator
    ├── github_service.py               # GitHub repository analysis & CV consistency checker
    └── enterprise_job_evaluation_service.py # Enterprise multi-category weighted evaluator
```

---

## 🧠 3. Core AI & Evaluation Engine Details

### 1. CV Extraction Engine (`cv_analysis_service.py` + `gemini_rest.py`)
- Reads raw text from PDF, DOCX, or TXT resumes using `pypdf` and `python-docx`.
- Formats text into a structured JSON schema constraint (`EXTRACTION_SCHEMA_JSON`).
- Invokes Gemini AI to extract candidate name, email, headline, work experience, technical skills, and soft skills into structured arrays.

### 2. GitHub Verification Engine (`github_evidence.py` + `github_service.py`)
- Queries GitHub's public REST API to fetch a candidate's public repositories, commit history, primary languages, and star counts.
- Compares claimed CV skills against actual repository language statistics to calculate a **CV Consistency Level** (*Verified*, *Partial*, or *Unverified*).

### 3. Multi-Category Weighted Evaluator (`enterprise_job_evaluation_service.py`)
- Calculates candidate match scores based on recruiter-configured category weights:
  - 📄 **CV Skills Match**: Direct overlap of required technical skills.
  - 🐙 **GitHub Evidence**: Real code evidence and repository complexity.
  - 💼 **Experience Match**: Candidate's years of experience vs. job requirement.
  - 🎓 **Education & Certifications**: Qualification levels and credentials.
- Outputs an overall candidate score (0–100), Match Level (*Strong Match*, *Moderate Match*, *Weak Match*), strengths, concerns, and missing skill gaps.

---

## 🛡️ 4. Response Optimization & Compression

All FastAPI responses are processed through `GZipMiddleware(minimum_size=1000)`.
- Reduces large JSON payload transfer sizes by **up to 80%**.
- Enables sub-millisecond response processing over HTTP.

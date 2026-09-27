from fastapi import FastAPI, Form, File, UploadFile, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError
import os
import sys
import certifi
import truststore
import hashlib
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlparse
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import ObjectId

# truststore's global SSL patch currently recurses inside Python 3.14 when
# PyMongo constructs its TLS context. Render is pinned to Python 3.13, and
# this guard also keeps startup safe if the runtime version is changed later.
if sys.version_info < (3, 14):
    truststore.inject_into_ssl()
os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", certifi.where())

import json
import io
import asyncio
import re
from dotenv import load_dotenv
from database import connect_database, db
from cv_document import extract_cv_content, find_github_profile_url, find_linkedin_profile_url, infer_cv_sections_from_text
from cv_normalization import (
    EXTRACTION_SCHEMA_JSON,
    SYSTEM_EXTRACTION_INSTRUCTION,
    is_soft_skill_candidate,
    normalize_cv_extraction,
)
from domain import AiEvaluation, CvExtraction, DEFAULT_WEIGHTS, ScoreWeights, audit, enriched_technical_skills, identifier, legacy_evaluation, reset_candidate_profile, save_extraction, save_manual_profile, utcnow, weighted_evaluation
from enterprise_evaluation import DEFAULT_BANDS, build_profile_scores, pending_decision, profile_totals, validate_bands, validate_requirements
from github_evidence import (
    GithubVerificationError,
    analyze_github_with_gemini,
    compare_github_snapshots,
    compute_cv_github_consistency,
    github_username,
    github_skill_records,
    verify_github_profile,
)
from linkedin_evidence import LinkedInVerificationError, authorization_url, configuration as linkedin_configuration, evidence_summary, exchange_and_collect, normalize_profile_url
from portfolio_evidence import PortfolioVerificationError, verify_portfolio
from gemini_rest import generate_json as generate_gemini_json

from repositories.candidate_repository import CandidateRepository
from repositories.github_repository import GithubRepository
from repositories.analysis_repository import AnalysisRepository
from repositories.job_repository import JobRepository

from services.github_service import GithubService
from services.scoring_service import ScoringService
from services.cv_analysis_service import CvAnalysisService
from services.job_match_service import JobMatchService
from services.enterprise_job_evaluation_service import SCORING_RULES_VERSION, evaluate as evaluate_enterprise_job, enterprise_scores
from services.candidate_service import CandidateService

# ==============================================================================
# STEP 1: Load Environment Variables & Secrets
# WHY THIS STEP: Keeps confidential credentials (like GEMINI_API_KEY) out of source code.
# ==============================================================================
load_dotenv()
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "CV" / ".env")

api_key = os.environ.get("GEMINI_API_KEY", "")
model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite") if api_key else None

# ==============================================================================
# STEP 2: Initialize Data Access Repositories (Tier 4)
# WHY THIS STEP: Provides clean CRUD interfaces for MongoDB collections.
# ==============================================================================
candidate_repo = CandidateRepository(db)
github_repo = GithubRepository(db)
analysis_repo = AnalysisRepository(db)
job_repo = JobRepository(db)

# ==============================================================================
# STEP 3: Initialize Domain Services & AI Engines (Tier 3)
# WHY THIS STEP: Encapsulates CV analysis, GitHub verification, and match calculations.
# ==============================================================================
github_service = GithubService(github_repo, api_key=api_key, model_name=model or "gemini-3.5-flash-lite")
scoring_service = ScoringService()
cv_analysis_service = CvAnalysisService(candidate_repo, analysis_repo, github_service, api_key=api_key, model_name=model or "gemini-3.5-flash-lite")
job_match_service = JobMatchService()
candidate_service = CandidateService(candidate_repo, analysis_repo, github_repo)

# ==============================================================================
# STEP 4: Create FastAPI Application & Static File Directory
# WHY THIS STEP: 'app' is the central ASGI application instance handling incoming HTTP requests.
# ==============================================================================
app = FastAPI(title="TalentVerifyAI API", version="1.0.0")

# Mount '/uploads' to serve uploaded files (e.g. candidate profile pictures)
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# ==============================================================================
# STEP 5: Configure Security & Performance Middleware
# ==============================================================================
# Parse allowed CORS origins so React on port 5173 can communicate with FastAPI on port 8000
cors_origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
).split(",") if origin.strip()]

from fastapi.middleware.gzip import GZipMiddleware

# WHY THIS STEP: CORSMiddleware allows browsers to make cross-origin requests safely
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WHY THIS STEP: GZipMiddleware compresses response payloads larger than 1KB by up to 80%,
# significantly speeding up network response times on the frontend!
app.add_middleware(GZipMiddleware, minimum_size=1000)

DEMO_RECRUITER = {"id": "demo-recruiter-1", "role": "recruiter", "name": "Demo Recruiter", "email": "recruiter@gmail.com", "company": "TalentVerify Technologies", "location": "Colombo, Sri Lanka", "bio": "Hiring talented people for growing technology teams."}
DEMO_CANDIDATE = {"id": "demo-candidate-1", "role": "candidate", "name": "Demo Candidate", "email": "candidate@gmail.com", "headline": "Software Developer", "location": "Colombo, Sri Lanka", "experience": 2, "skills": "React, JavaScript, Python, SQL, Communication, Problem Solving", "technicalSkills": "React, JavaScript, Python, SQL", "softSkills": "Communication, Problem Solving, Teamwork", "bio": "Software developer interested in building reliable web applications."}
INITIAL_JOBS = [
    {"id": "tv-job-fullstack", "recruiterId": DEMO_RECRUITER["id"], "title": "Full Stack Developer", "company": "TalentVerify Technologies", "location": "Colombo", "type": "Full-time", "experience": 3, "skills": "React, TypeScript, Node.js, PostgreSQL, REST API, Docker", "description": "Build secure customer-facing products across modern frontend and backend services.", "createdAt": "2026-09-02"},
    {"id": "tv-job-ai-engineer", "recruiterId": DEMO_RECRUITER["id"], "title": "AI / ML Engineer", "company": "TalentVerify Technologies", "location": "Remote", "type": "Full-time", "experience": 2, "skills": "Python, Machine Learning, TensorFlow, NLP, FastAPI, SQL", "description": "Develop, evaluate, and deploy intelligent matching and document-analysis models.", "createdAt": "2026-09-02"},
    {"id": "tv-job-qa", "recruiterId": DEMO_RECRUITER["id"], "title": "QA Automation Engineer", "company": "TalentVerify Technologies", "location": "Kandy", "type": "Full-time", "experience": 2, "skills": "Selenium, Playwright, JavaScript, API Testing, SQL, CI/CD", "description": "Own automated test coverage for web applications, APIs, and release pipelines.", "createdAt": "2026-09-02"},
    {"id": "tv-job-devops", "recruiterId": DEMO_RECRUITER["id"], "title": "DevOps Engineer", "company": "TalentVerify Technologies", "location": "Colombo", "type": "Full-time", "experience": 3, "skills": "AWS, Docker, Kubernetes, Terraform, Linux, CI/CD", "description": "Operate cloud infrastructure and improve deployment reliability, security, and observability.", "createdAt": "2026-09-02"},
]


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210000).hex()
    return f"{salt}${digest}"


def verify_password(password, stored):
    try:
        salt, _ = stored.split("$", 1)
        return secrets.compare_digest(hash_password(password, salt), stored)
    except (AttributeError, ValueError):
        return False


def public_document(document):
    """Recursively make MongoDB records safe for API responses and immutable snapshots."""
    if document is None:
        return None
    if isinstance(document, ObjectId):
        return str(document)
    if isinstance(document, dict):
        return {key: public_document(value) for key, value in document.items() if key not in {"_id", "passwordHash"}}
    if isinstance(document, (list, tuple)):
        return [public_document(value) for value in document]
    return document


def safe_portfolio_url(value):
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else None
    except ValueError:
        return None


def safe_linkedin_url(value):
    return normalize_profile_url(value)


def ensure_database():
    try:
        connect_database()
        candidate_repo.ensure_indexes()
        github_repo.ensure_indexes()
        analysis_repo.ensure_indexes()
        job_repo.ensure_indexes()

        db.users.create_index("email", unique=True)
        db.sessions.create_index("token", unique=True)
        db.applications.create_index([("userId", 1), ("jobId", 1)], unique=True)
        db.jobs.create_index("recruiterId")
        db.candidates.create_index("user_id", unique=True)
        db.candidates.create_index("id", unique=True)
        db.linkedin_oauth_states.create_index("created_at", expireAfterSeconds=600)
        db.recruiters.create_index("user_id", unique=True)
        db.recruiters.create_index("id", unique=True)
        db.candidate_skills.create_index([("candidate_id", 1), ("normalized_skill", 1), ("source", 1)], unique=True)
        db.candidate_experience.create_index("candidate_id")
        db.candidate_education.create_index("candidate_id")
        db.candidate_projects.create_index("candidate_id")
        db.candidate_certifications.create_index("candidate_id")
        db.candidate_languages.create_index([("candidate_id", 1), ("language", 1), ("source", 1)])
        db.cv_documents.create_index("candidate_id")
        db.candidate_github.create_index("candidate_id", unique=True)
        db.github_evidence_snapshots.create_index([("candidate_id", 1), ("scanned_at", -1)])
        db.ai_evaluations.create_index("application_id")
        db.interviews.create_index("application_id")
        db.technical_assessments.create_index("application_id")
        db.final_decisions.create_index("application_id")
        db.score_overrides.create_index([("application_id", 1), ("category", 1)])
        db.audit_logs.create_index([("entity_type", 1), ("entity_id", 1), ("created_at", -1)])
        for account in (DEMO_RECRUITER, DEMO_CANDIDATE):
            db.users.update_one(
                {"id": account["id"]},
                {"$setOnInsert": {**account, "passwordHash": hash_password("abcd123@")}},
                upsert=True,
            )
        for job in INITIAL_JOBS:
            db.jobs.update_one({"id": job["id"]}, {"$setOnInsert": job}, upsert=True)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. Start MongoDB or check MONGODB_URI.") from exc


@app.on_event("startup")
def startup_database():
    ensure_database()


@app.get("/api/health")
def health():
    ensure_database()
    return {
        "status": "ok",
        "database": "connected",
        "app_dir": str(Path(__file__).resolve().parent),
    }


def require_session(token):
    ensure_database()
    session = db.sessions.find_one({"token": token})
    if not session:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    user_id = session.get("userId") or session.get("user_id")
    user = db.users.find_one({"id": user_id}) if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail="Session user no longer exists.")
    return public_document(user)


class AuthRequest(BaseModel):
    mode: str
    role: str = ""
    name: str = ""
    email: str
    password: str
    company: str = ""
    location: str = ""


class DocumentPayload(BaseModel):
    data: dict


@app.post("/api/auth")
def authenticate(payload: AuthRequest):
    ensure_database()
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must contain at least 6 characters.")
    if payload.mode == "register":
        if payload.role not in {"candidate", "recruiter"}:
            raise HTTPException(status_code=400, detail="Invalid account type.")
        account = {"id": str(uuid.uuid4()), "role": payload.role, "name": payload.name.strip(), "email": email, "passwordHash": hash_password(payload.password)}
        if payload.role == "recruiter":
            account.update({"company": payload.company.strip(), "location": payload.location.strip()})
        try:
            db.users.insert_one(account)
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail="An account already exists with this email.") from exc
    else:
        account = db.users.find_one({"email": email})
        if not account or not verify_password(payload.password, account.get("passwordHash")):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    token = secrets.token_urlsafe(32)
    db.sessions.insert_one({"token": token, "userId": account["id"], "createdAt": datetime.now(timezone.utc)})
    return {"token": token, "user": public_document(account)}


@app.get("/api/app-data")
def app_data(token: str):
    user = require_session(token)
    jobs = [public_document(item) for item in db.jobs.find().sort("createdAt", -1)]
    if user["role"] == "recruiter":
        job_ids = [job["id"] for job in jobs if job.get("recruiterId") == user["id"]]
        applications = [public_document(item) for item in db.applications.find({"jobId": {"$in": job_ids}}).sort("appliedAt", -1)]
        for application in applications:
            snapshot = application.setdefault("candidateSnapshot", {})
            candidate = db.users.find_one({"id": application.get("userId")}, {"portfolioUrl": 1, "linkedinUrl": 1}) or {}
            profile = db.candidates.find_one({"id": application.get("userId")}, {"social_links": 1}) or {}
            if not snapshot.get("portfolioUrl"):
                snapshot["portfolioUrl"] = safe_portfolio_url(candidate.get("portfolioUrl") or (profile.get("social_links") or {}).get("portfolio_url"))
            if not snapshot.get("linkedinUrl"):
                snapshot["linkedinUrl"] = safe_linkedin_url(candidate.get("linkedinUrl") or (profile.get("social_links") or {}).get("linkedin_url"))
            candidate_privacy = db.users.find_one({"id": application.get("userId")}, {"profilePhotoUrl": 1, "photoVisibleToRecruiters": 1}) or {}
            snapshot["profilePhotoUrl"] = candidate_privacy.get("profilePhotoUrl") if candidate_privacy.get("photoVisibleToRecruiters") is True else None
            snapshot["photoVisibleToRecruiters"] = candidate_privacy.get("photoVisibleToRecruiters") is True
    else:
        applications = [public_document(item) for item in db.applications.find({"userId": user["id"]}).sort("appliedAt", -1)]
    return {"user": user, "jobs": jobs, "applications": applications}


@app.patch("/api/profile")
def update_profile(payload: DocumentPayload, token: str):
    user = require_session(token)
    allowed = {"name", "phone", "location", "headline", "company", "bio", "skills", "technicalSkills", "softSkills", "experience", "cvFileName", "cvImportedAt", "linkedinUrl", "githubUrl", "portfolioUrl", "noticePeriod", "availability", "preferredRoles", "workPreference", "photoVisibleToRecruiters"}
    changes = {key: value for key, value in payload.data.items() if key in allowed}
    if "portfolioUrl" in changes and changes["portfolioUrl"]:
        validated_portfolio = safe_portfolio_url(changes["portfolioUrl"])
        if not validated_portfolio:
            raise HTTPException(status_code=422, detail="Portfolio URL must be a valid http:// or https:// link.")
        changes["portfolioUrl"] = validated_portfolio

    if ("technicalSkills" in changes or "softSkills" in changes) and "skills" not in changes:
        tech = changes.get("technicalSkills") if "technicalSkills" in changes else user.get("technicalSkills", "")
        soft = changes.get("softSkills") if "softSkills" in changes else user.get("softSkills", "")
        tech_list = [s.strip() for s in str(tech or "").split(",") if s.strip()]
        soft_list = [s.strip() for s in str(soft or "").split(",") if s.strip()]
        combined = []
        seen = set()
        for item in tech_list + soft_list:
            if item.casefold() not in seen:
                seen.add(item.casefold())
                combined.append(item)
        changes["skills"] = ", ".join(combined)

    save_manual_profile(db, user, changes)
    updated = db.users.find_one_and_update({"id": user["id"]}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    return public_document(updated)


@app.post("/api/candidates/me/reset-profile")
def reset_candidate_profile_route(token: str):
    user = require_session(token)
    if user.get("role") != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can reset their profile.")
    result = reset_candidate_profile(db, user["id"])
    return result


@app.post("/api/profile/photo")
async def upload_profile_photo(file: UploadFile = File(...), token: str = ""):
    user = require_session(token)
    content_types = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    extension = content_types.get((file.content_type or "").lower())
    if not extension:
        raise HTTPException(status_code=422, detail="Profile photo must be a JPG, PNG, or WebP image.")
    content = await file.read(2 * 1024 * 1024 + 1)
    if not content or len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Profile photo must be 2 MB or smaller.")
    filename = f"profile-{user['id']}-{uuid.uuid4().hex}{extension}"
    path = UPLOADS_DIR / filename
    path.write_bytes(content)
    photo_url = f"/uploads/{filename}"
    db.users.update_one({"id": user["id"]}, {"$set": {"profilePhotoUrl": photo_url}})
    collection = "candidates" if user["role"] == "candidate" else "recruiters"
    db[collection].update_one({"id": user["id"]}, {"$set": {"profile_photo_url": photo_url, "updated_at": utcnow()}}, upsert=True)
    audit(db, "profile_photo_uploaded", user["id"], user["role"], user["id"], {"content_type": file.content_type})
    return {"profilePhotoUrl": photo_url}


async def save_verified_github(candidate_id: str, value: str, job: dict = None):
    try:
        raw_evidence = await asyncio.to_thread(verify_github_profile, value)
    except GithubVerificationError as exc:
        # Mark refresh failed if a snapshot already exists, but DO NOT delete existing snapshot
        existing = db.candidate_github.find_one({"candidate_id": candidate_id})
        if existing:
            requested_username = github_username(value)
            cached_username = str(existing.get("username") or "").casefold()
            db.candidate_github.update_one(
                {"candidate_id": candidate_id},
                {"$set": {"latest_refresh_failed": True, "last_failed_refresh_at": utcnow().isoformat(), "refresh_failure_reason": str(exc)}}
            )
            # A transient API limit must not discard already verified evidence.
            # Only reuse it when it belongs to the profile currently requested.
            if "rate limit" in str(exc).casefold() and cached_username == requested_username.casefold():
                existing.update({
                    "latest_refresh_failed": True,
                    "evidence_source_status": "cached",
                    "refresh_failure_reason": str(exc),
                })
                return existing
        raise

    # GitHub-detected technologies are evidence, not candidate profile claims.
    # Excluding them prevents each refresh from feeding repository discoveries
    # back into the CV consistency list and expanding it indefinitely.
    skills = list(db.candidate_skills.find({
        "candidate_id": candidate_id,
        "kind": "technical",
        "source": {"$ne": "github_api"},
    }))
    cv_skill_names = [s.get("skill") for s in skills if s.get("skill")]
    cv_consistency = compute_cv_github_consistency(cv_skill_names, raw_evidence.get("detected_skills", []))
    
    gemini_analysis = await analyze_github_with_gemini(
        raw_evidence,
        cv_skill_names,
        job=job,
        api_key=api_key,
        model_name=model or "gemini-3.5-flash-lite"
    )

    snapshot_id = identifier()
    snapshot_doc = {
        "id": snapshot_id,
        "_id": snapshot_id,
        "candidate_id": candidate_id,
        "github_url": raw_evidence["github_url"],
        "username": raw_evidence["username"],
        "scanned_at": raw_evidence["verified_at"],
        "profile": raw_evidence.get("profile"),
        "repositories": raw_evidence.get("repositories", []),
        "detected_skills": raw_evidence.get("detected_skills", []),
        "skill_evidence": raw_evidence.get("skill_evidence", []),
        "cv_consistency": cv_consistency,
        "analysis": gemini_analysis,
        "raw_analysis": raw_evidence.get("analysis", {}),
        "api_status": "success",
        "github_data_version": 1,
    }
    # Immutable historical snapshot: Never overwrite old snapshots
    db.github_evidence_snapshots.insert_one(snapshot_doc)

    pointer_doc = {
        "candidate_id": candidate_id,
        "github_url": raw_evidence["github_url"],
        "username": raw_evidence["username"],
        "latest_snapshot_id": snapshot_id,
        "last_verified_at": raw_evidence["verified_at"],
        "verification_status": "verified",
        "latest_refresh_failed": False,
        "analysis": gemini_analysis,
        "raw_analysis": raw_evidence.get("analysis", {}),
        "detected_skills": raw_evidence.get("detected_skills", []),
        "skill_evidence": raw_evidence.get("skill_evidence", []),
        "cv_consistency": cv_consistency,
        "repositories": raw_evidence.get("repositories", []),
        "profile": raw_evidence.get("profile"),
    }
    db.candidate_github.update_one({"candidate_id": candidate_id}, {"$set": pointer_doc}, upsert=True)
    db.candidates.update_one({"id": candidate_id}, {"$set": {
        "github": {
            "profile_url": raw_evidence["github_url"],
            "username": raw_evidence["username"],
            "latest_snapshot_id": snapshot_id,
            "last_verified_at": raw_evidence["verified_at"],
            "verification_status": "verified"
        }
    }})

    db.candidate_skills.delete_many({"candidate_id": candidate_id, "source": "github_api"})
    records = github_skill_records(candidate_id, snapshot_doc)
    if records:
        db.candidate_skills.insert_many(records)

    db.users.update_one({"id": candidate_id}, {"$set": {"githubUrl": raw_evidence["github_url"]}})
    audit(db, "github_evidence_verified", candidate_id, "candidate", candidate_id, {
        "snapshot_id": snapshot_id,
        "username": raw_evidence["username"],
        "repositories": len(raw_evidence.get("repositories", [])),
        "detected_skills": len(raw_evidence.get("detected_skills", [])),
    })
    return snapshot_doc


def apply_verified_github_score(scores, weights, github_snapshot, job):
    """Authoritatively calculate GitHub score strictly using backend verified data and configured weight."""
    if not github_snapshot or github_snapshot.get("verification_status") not in {"verified", "success"}:
        return scores, round(sum(float(item.get("weighted_score", 0)) for item in scores.values()), 2)
    
    analysis = github_snapshot.get("analysis") or {}
    gemini_match = float(analysis.get("match_percentage", 0))
    maximum = float(weights.github_evidence)
    # Authoritative calculation: weighted_score = (match_percentage / 100) * github_weight
    weighted = round((gemini_match / 100.0) * maximum, 2)
    
    detected_skills = github_snapshot.get("detected_skills") or []
    consistency = (github_snapshot.get("cv_consistency") or {}).get("consistency_level", "verified")
    snapshot_id = github_snapshot.get("id") or github_snapshot.get("latest_snapshot_id", "latest")

    scores["github_evidence"] = {
        "match_percentage": gemini_match,
        "weighted_score": weighted,
        "maximum_score": maximum,
        "reason": f"Evaluated from GitHub API-verified evidence ({gemini_match}% technical match).",
        "evidence": [
            f"Verified skills: {', '.join(detected_skills[:6])}" if detected_skills else "Active public profile",
            f"CV Consistency: {consistency.replace('_', ' ').title()}",
            f"GitHub Snapshot ID: {snapshot_id[:8]}",
        ],
        "confidence": float(analysis.get("confidence", 0.93)),
    }
    return scores, round(sum(float(item.get("weighted_score", 0)) for item in scores.values()), 2)


@app.post("/api/candidates/me/github/verify")
async def verify_candidate_github(payload: DocumentPayload, token: str):
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can verify GitHub evidence.")
    value = payload.data.get("github_url") or payload.data.get("username")
    try:
        snapshot = await save_verified_github(user["id"], value)
        return public_document(snapshot)
    except GithubVerificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def save_linkedin_profile_url(candidate_id, value, source="candidate"):
    canonical = safe_linkedin_url(value)
    if not canonical:
        raise LinkedInVerificationError("Enter a valid public LinkedIn profile URL such as https://www.linkedin.com/in/username.")
    verification = {
        "url": canonical,
        "verification_status": "verified_url",
        "verified_at": utcnow().isoformat(),
        "evidence_type": "linkedin_profile_url",
        "source": source,
        "ownership_verified": False,
    }
    db.users.update_one({"id": candidate_id}, {"$set": {"linkedinUrl": canonical, "linkedinVerification": verification}})
    db.candidate_profiles.update_one({"id": candidate_id}, {"$set": {
        "social_links.linkedin_url": canonical, "linkedin_verification": verification}}, upsert=True)
    audit(db, "linkedin_url_verified", candidate_id, "candidate", candidate_id, {"url": canonical, "source": source})
    return verification


@app.post("/api/candidates/me/linkedin/verify")
def verify_candidate_linkedin(payload: DocumentPayload, token: str):
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can verify LinkedIn evidence.")
    try:
        return save_linkedin_profile_url(user["id"], payload.data.get("linkedin_url"))
    except LinkedInVerificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/candidates/me/portfolio/verify")
def verify_candidate_portfolio(payload: DocumentPayload, token: str):
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can verify portfolio evidence.")
    value = safe_portfolio_url(payload.data.get("portfolio_url"))
    if not value:
        raise HTTPException(status_code=422, detail="Enter a valid portfolio URL including http:// or https://.")
    claimed = [part.strip() for part in f"{user.get('technicalSkills', '')},{user.get('softSkills', '')}".split(",") if part.strip()]
    try:
        evidence = verify_portfolio(value, claimed)
    except PortfolioVerificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    evidence.update({"id": identifier(), "candidate_id": user["id"], "verified_at": utcnow().isoformat(), "source": "public_portfolio"})
    db.portfolio_evidence_snapshots.insert_one(evidence.copy())
    db.users.update_one({"id": user["id"]}, {"$set": {"portfolioUrl": evidence["portfolio_url"], "portfolioVerification": evidence}})
    db.candidate_profiles.update_one({"id": user["id"]}, {"$set": {"social_links.portfolio_url": evidence["portfolio_url"], "portfolio_verification": evidence}})
    audit(db, "portfolio_evidence_collected", user["id"], "candidate", user["id"], {"url": evidence["portfolio_url"], "matched_skills": [x["skill"] for x in evidence["matched_skills"]]})
    return public_document(evidence)


@app.post("/api/candidates/me/linkedin/connect")
def connect_candidate_linkedin(token: str):
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can connect LinkedIn evidence.")
    state = secrets.token_urlsafe(32)
    try:
        auth_url = authorization_url(state)
    except LinkedInVerificationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.linkedin_oauth_states.insert_one({"state": state, "candidate_id": user["id"], "created_at": utcnow()})
    return {"authorization_url": auth_url}


@app.get("/api/linkedin/oauth/callback")
def linkedin_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    config = linkedin_configuration()
    target = f"{config['frontend_url']}/?{urlencode({'linkedin': 'error' if error else 'verified'})}"
    if error or not code or not state:
        return RedirectResponse(target)
    oauth_state = db.linkedin_oauth_states.find_one_and_delete({"state": state})
    if not oauth_state:
        return RedirectResponse(f"{config['frontend_url']}/?linkedin=invalid_state")
    created_at = oauth_state.get("created_at")
    if not created_at or created_at < utcnow() - timedelta(minutes=10):
        return RedirectResponse(f"{config['frontend_url']}/?linkedin=expired_state")
    try:
        identity, report = exchange_and_collect(code)
        now = utcnow()
        evidence = evidence_summary(identity, report, now.isoformat(), config["tier"])
        evidence.update({"candidate_id": oauth_state["candidate_id"], "id": identifier()})
        db.linkedin_evidence_snapshots.insert_one(evidence.copy())
        db.users.update_one({"id": oauth_state["candidate_id"]}, {"$set": {"linkedinVerification": evidence,
            **({"linkedinUrl": evidence["profile_url"]} if evidence.get("profile_url") else {})}})
        db.candidate_profiles.update_one({"id": oauth_state["candidate_id"]}, {"$set": {"linkedin_verification": evidence,
            **({"social_links.linkedin_url": evidence["profile_url"]} if evidence.get("profile_url") else {})}})
        audit(db, "linkedin_api_evidence_collected", oauth_state["candidate_id"], "candidate", oauth_state["candidate_id"],
              {"categories": evidence["verified_categories"], "tier": config["tier"]})
    except LinkedInVerificationError:
        return RedirectResponse(f"{config['frontend_url']}/?linkedin=api_error")
    return RedirectResponse(target)


@app.get("/api/candidates/me/linkedin/evidence")
def get_candidate_linkedin_evidence(token: str):
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can view their LinkedIn evidence.")
    history = [public_document(item) for item in db.linkedin_evidence_snapshots.find(
        {"candidate_id": user["id"]}).sort("verified_at", -1).limit(10)]
    if history:
        return {"latest": history[0], "history": history, "using_cached_evidence": True}
    return {"latest": {
        "verification_status": "not_connected",
        "skills": {"status": "unsupported_by_linkedin_api", "evidence": []},
        "soft_skills": {"status": "unsupported_by_linkedin_api", "evidence": []},
        "certifications": {"status": "unsupported_by_linkedin_api", "evidence": []},
        "education": {"status": "requires_linkedin_plus", "evidence": None},
    }, "history": [], "using_cached_evidence": False}


@app.get("/api/jobs")
def list_jobs(token: str):
    require_session(token)
    return [public_document(item) for item in db.jobs.find().sort("createdAt", -1)]


@app.get("/api/public/jobs")
def list_public_jobs():
    ensure_database()
    fields = {"_id": 0, "id": 1, "title": 1, "company": 1, "location": 1, "type": 1, "experience": 1, "skills": 1, "description": 1, "createdAt": 1}
    return list(db.jobs.find({}, fields).sort("createdAt", -1))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, token: str):
    require_session(token)
    job = db.jobs.find_one({"id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return public_document(job)


@app.post("/api/jobs", status_code=status.HTTP_201_CREATED)
def create_job(payload: DocumentPayload, token: str):
    user = require_session(token)
    if user["role"] != "recruiter":
        raise HTTPException(status_code=403, detail="Only recruiters can create jobs.")
    try:
        weights = ScoreWeights.model_validate(payload.data.get("scoring_weights", DEFAULT_WEIGHTS)).model_dump()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    requirements = payload.data.get("requirements") or {"required_skills": [x.strip() for x in str(payload.data.get("skills", "")).split(",") if x.strip()], "preferred_skills": [], "minimum_experience_years": float(payload.data.get("experience") or 0), "education": {"minimum_level": None}}
    try:
        must_haves = validate_requirements(payload.data.get("must_have_requirements", []))
        bands = validate_bands(payload.data.get("recommendation_bands"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = {**payload.data, "requirements": requirements, "must_have_requirements": must_haves,
           "recommendation_bands": bands, "scoring_weights": weights, "requirements_version": 1,
           "weight_configuration_version": 1, "id": str(uuid.uuid4()), "recruiterId": user["id"],
           "company": payload.data.get("company") or user.get("company") or "Company", "createdAt": datetime.now(timezone.utc).date().isoformat()}
    db.jobs.insert_one(job)
    db.job_requirements.update_one({"job_id": job["id"]}, {"$set": {"job_id": job["id"], **requirements, "updated_at": utcnow()}}, upsert=True)
    audit(db, "job_created", user["id"], "job", job["id"], {"scoring_weights": weights})
    return public_document(job)


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: str, payload: DocumentPayload, token: str):
    user = require_session(token)
    changes = {key: value for key, value in payload.data.items() if key in {"title", "company", "location", "type", "experience", "skills", "description", "requirements", "must_have_requirements", "recommendation_bands", "scoring_weights"}}
    if "scoring_weights" in changes:
        try: changes["scoring_weights"] = ScoreWeights.model_validate(changes["scoring_weights"]).model_dump()
        except ValidationError as exc: raise HTTPException(status_code=422, detail=exc.errors()) from exc
        changes["weight_configuration_version"] = int((db.jobs.find_one({"id": job_id}) or {}).get("weight_configuration_version", 1)) + 1
    try:
        if "must_have_requirements" in changes: changes["must_have_requirements"] = validate_requirements(changes["must_have_requirements"])
        if "recommendation_bands" in changes: changes["recommendation_bands"] = validate_bands(changes["recommendation_bands"])
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "requirements" in changes or "must_have_requirements" in changes:
        changes["requirements_version"] = int((db.jobs.find_one({"id": job_id}) or {}).get("requirements_version", 1)) + 1
    updated = db.jobs.find_one_and_update({"id": job_id, "recruiterId": user["id"]}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found.")
    if "requirements" in changes:
        db.job_requirements.update_one({"job_id": job_id}, {"$set": {"job_id": job_id, **changes["requirements"], "updated_at": utcnow()}}, upsert=True)
    audit(db, "job_updated", user["id"], "job", job_id, {"fields": list(changes)})
    return public_document(updated)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, token: str):
    user = require_session(token)
    if user["role"] != "recruiter":
        raise HTTPException(status_code=403, detail="Only recruiters can delete jobs.")
    result = db.jobs.delete_one({"id": job_id, "recruiterId": user["id"]})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Job not found.")
    db.applications.delete_many({"jobId": job_id})
    return {"ok": True}


@app.post("/api/jobs/{job_id}/candidate-evaluation")
async def preview_candidate_evaluation(job_id: str, token: str):
    """Database-backed preview; the application endpoint recalculates authoritatively on submit."""
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can preview their job match.")
    job = db.jobs.find_one({"id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    candidate = candidate_service.get_complete_candidate_profile(user["id"]) or {}
    github_snapshot = github_repo.get_latest_github_profile(user["id"])
    candidate["github"] = github_snapshot
    candidate["portfolio_evidence"] = public_document(db.portfolio_evidence_snapshots.find_one(
        {"candidate_id": user["id"]}, sort=[("verified_at", -1)]))
    candidate["linkedin_evidence"] = public_document(db.linkedin_evidence_snapshots.find_one(
        {"candidate_id": user["id"]}, sort=[("verified_at", -1)]))
    candidate["cv_records"] = [public_document(item) for item in db.cv_documents.find(
        {"candidate_id": user["id"]}, {"_id": 0, "content": 0, "raw_gemini_result": 0}).sort("uploaded_at", -1).limit(3)]
    for excluded in ("profilePhotoUrl", "photo_url", "date_of_birth", "gender", "marital_status"):
        candidate.pop(excluded, None)
    result, provider = await evaluate_enterprise_job(public_document(job), candidate, generate_json if model else None)
    return {**result, "score": result["candidate_score"], "evaluation_provider": provider,
            "scoring_rules_version": SCORING_RULES_VERSION,
            "preview": True, "authoritative_score_calculated_on_application": True}


@app.post("/api/applications", status_code=status.HTTP_201_CREATED)
async def create_application(payload: DocumentPayload, token: str):
    user = require_session(token)
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Only candidates can apply for jobs.")
    job = db.jobs.find_one({"id": payload.data.get("jobId")})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    existing = db.applications.find_one({"userId": user["id"], "jobId": payload.data.get("jobId")})
    if existing:
        raise HTTPException(status_code=409, detail="You already applied for this job.")
    
    # Retrieve Candidate Intelligence Profile & GitHub data
    cand_profile = candidate_service.get_complete_candidate_profile(user["id"]) or {}
    github_snapshot = github_repo.get_latest_github_profile(user["id"])
    cand_profile["github"] = github_snapshot
    cand_profile["portfolio_evidence"] = public_document(db.portfolio_evidence_snapshots.find_one(
        {"candidate_id": user["id"]}, sort=[("verified_at", -1)]))
    cand_profile["linkedin_evidence"] = public_document(db.linkedin_evidence_snapshots.find_one(
        {"candidate_id": user["id"]}, sort=[("verified_at", -1)]))
    cand_profile["cv_records"] = [public_document(item) for item in db.cv_documents.find(
        {"candidate_id": user["id"]}, {"_id": 0, "content": 0, "raw_gemini_result": 0}).sort("uploaded_at", -1).limit(3)]
    # Appearance and other irrelevant personal attributes never enter the evaluator.
    for excluded in ("profilePhotoUrl", "photo_url", "date_of_birth", "gender", "marital_status"):
        cand_profile.pop(excluded, None)

    # Calculate Candidate Profile Score (out of 100)
    cand_prof_score_res = scoring_service.calculate_candidate_profile_score(cand_profile, github_snapshot)
    
    # Calculate Job Match Score (out of 100) separately
    job_match_res = job_match_service.calculate_job_match_score(cand_profile, job, github_snapshot, cand_prof_score_res)
    enterprise_result, evaluation_provider = await evaluate_enterprise_job(
        public_document(job), cand_profile, generate_json if model else None)
    enterprise_score_map = enterprise_scores(enterprise_result)

    snapshot_id = (github_snapshot or {}).get("id") or (github_snapshot or {}).get("latest_snapshot_id")
    has_verified_github = bool(github_snapshot and github_snapshot.get("verification_status") in {"verified", "success"})
    
    application = {
        **payload.data,
        "id": str(uuid.uuid4()),
        "candidateId": user["id"],
        "userId": user["id"],
        "status": "Under review",
        "candidateProfileScore": cand_prof_score_res["overall_score"],
        "candidateProfileScoreBreakdown": cand_prof_score_res["score_breakdown"],
        "jobMatchScore": enterprise_result["candidate_score"],
        "score": enterprise_result["candidate_score"],
        "jobMatchScoreBreakdown": enterprise_result["score_breakdown"],
        "evidenceConfidence": enterprise_result["evidence_confidence"],
        "matchLevel": enterprise_result.get("match_level"),
        "recommendedDecision": (enterprise_result.get("recommendation") or {}).get("decision"),
        "recommendation": enterprise_result.get("recommendation"),
        "mandatoryRequirements": enterprise_result.get("mandatory_requirements", []),
        "skillAnalysis": enterprise_result.get("skill_analysis", []),
        "strengths": enterprise_result.get("candidate_strengths", []),
        "weaknesses": enterprise_result.get("concerns", []),
        "missingRequirements": [*enterprise_result.get("skill_gaps", []), *enterprise_result.get("experience_gaps", [])],
        "verifiedSkills": job_match_res["verified_skills"],
        "unverifiedClaims": job_match_res["unverified_claims"],
        "interviewFocus": job_match_res["interview_focus"],
        "scores": enterprise_score_map,
        "overallScore": enterprise_result["candidate_score"],
        "enterpriseEvaluation": enterprise_result,
        "analysis": enterprise_result,
        "githubEvidence": github_snapshot if has_verified_github else None,
        "githubSnapshotId": snapshot_id if has_verified_github else None,
        "evaluationVersion": 1,
        "scoringRulesVersion": SCORING_RULES_VERSION,
        "finalDecision": pending_decision(),
        "appliedAt": datetime.now(timezone.utc).isoformat()
    }
    submitted_snapshot = application.get("candidateSnapshot") if isinstance(application.get("candidateSnapshot"), dict) else {}
    application["candidateSnapshot"] = {**submitted_snapshot,
        "portfolioUrl": safe_portfolio_url(user.get("portfolioUrl") or (cand_profile.get("social_links") or {}).get("portfolio_url")),
        "linkedinUrl": safe_linkedin_url(user.get("linkedinUrl") or (cand_profile.get("social_links") or {}).get("linkedin_url")),
        "profilePhotoUrl": user.get("profilePhotoUrl") if user.get("photoVisibleToRecruiters") is True else None,
        "photoVisibleToRecruiters": user.get("photoVisibleToRecruiters") is True}
    db.applications.insert_one(application)

    # Store immutable analysis_run audit record
    run_id = f"run_app_{application['id']}_{int(utcnow().timestamp())}"
    analysis_run_doc = {
        "id": run_id,
        "candidate_id": user["id"],
        "job_id": job["id"],
        "application_id": application["id"],
        "document_version": 1,
        "extracted_data_snapshot": cand_profile,
        "verification_results": {
            "github_verified": has_verified_github,
            "github_snapshot_id": snapshot_id,
        },
        "scoring_rules_version": SCORING_RULES_VERSION,
        "model_version": None,
        "candidate_profile_score": cand_prof_score_res["overall_score"],
        "job_match_score": enterprise_result["candidate_score"],
        "recommended_decision": (enterprise_result.get("recommendation") or {}).get("decision"),
        "individual_score_components": enterprise_result["score_breakdown"],
        "evidence": {
            "strengths": enterprise_result.get("candidate_strengths", []),
            "skill_analysis": enterprise_result.get("skill_analysis", []),
            "verification_gaps": enterprise_result.get("verification_gaps", []),
        },
        "analysis_timestamp": utcnow(),
    }
    analysis_repo.record_analysis_run(analysis_run_doc)

    weights = ScoreWeights.model_validate(job.get("scoring_weights", DEFAULT_WEIGHTS))
    raw_evaluation = enterprise_result
    scores = enterprise_score_map
    pre_interview = enterprise_result["candidate_score"]
    profile_summary = {"score": pre_interview, "maximum": 100, "normalized_percentage": pre_interview}
    
    evaluation = {
        "id": identifier(),
        "application_id": application["id"],
        "candidate_id": user["id"],
        "job_id": job["id"],
        "ai_provider": evaluation_provider,
        "model": None,
        "scoring_rules_version": SCORING_RULES_VERSION,
        "scores": scores,
        "pre_interview_score": pre_interview,
        "profile_score": profile_summary,
        "raw_result": raw_evaluation,
        "evaluation_version": 1,
        "previous_evaluation_id": None,
        "github_snapshot_id": snapshot_id if has_verified_github else None,
        "evaluated_at": utcnow().isoformat(),
        "created_at": utcnow(),
        "evidence_confidence": enterprise_result["evidence_confidence"],
        "mandatory_requirements": enterprise_result.get("mandatory_requirements", []),
        "skill_analysis": enterprise_result.get("skill_analysis", []),
        "audit_snapshot": {"candidate_profile_version": cand_profile.get("version", 1), "cv_version": 1,
            "job_requirements_version": job.get("requirements_version", 1), "weight_configuration_version": job.get("weight_configuration_version", 1),
            "github_snapshot_id": snapshot_id if has_verified_github else None,
            "scoring_rules_version": SCORING_RULES_VERSION},
    }
    db.ai_evaluations.insert_one(evaluation)
    db.applications.update_one({"id": application["id"]}, {"$set": {
        "scores": scores, "profileScore": profile_summary["score"], "profileMaximum": profile_summary["maximum"],
        "normalizedProfilePercentage": profile_summary["normalized_percentage"], "overallScore": profile_summary["score"],
        "evaluationVersion": 1, "evaluationStage": "enterprise_job_match",
        "evidenceConfidence": enterprise_result["evidence_confidence"],
        "enterpriseEvaluation": enterprise_result,
        "aiAssistedMatchAssessment": (enterprise_result.get("recommendation") or {}).get("decision")}})
    audit(db, "application_evaluated", user["id"], "application", application["id"], {
        "evaluation_id": evaluation["id"],
        "candidate_profile_score": cand_prof_score_res["overall_score"],
        "job_match_score": enterprise_result["candidate_score"],
        "evidence_confidence": enterprise_result["evidence_confidence"],
        "recommended_decision": (enterprise_result.get("recommendation") or {}).get("decision")
    })
    return public_document(db.applications.find_one({"id": application["id"]}))


@app.post("/api/applications/{application_id}/re-evaluate")
async def re_evaluate_application(application_id: str, token: str):
    user = require_session(token)
    application = db.applications.find_one({"id": application_id})
    if not application:
        raise HTTPException(status_code=404, detail="Application not found.")
    if user["role"] == "candidate" and application.get("userId") != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    if user["role"] == "recruiter":
        job_check = db.jobs.find_one({"id": application["jobId"], "recruiterId": user["id"]})
        if not job_check:
            raise HTTPException(status_code=403, detail="Job not found under your recruiter account.")

    job = db.jobs.find_one({"id": application["jobId"]})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    candidate_id = application["userId"]
    latest_eval = db.ai_evaluations.find_one({"application_id": application_id}, sort=[("created_at", -1)])
    next_version = (int(latest_eval.get("evaluation_version", 1)) if latest_eval else 1) + 1

    # Fetch latest GitHub snapshot
    github_snapshot = public_document(db.github_evidence_snapshots.find_one({"candidate_id": candidate_id, "api_status": "success"}, sort=[("scanned_at", -1)]))
    if not github_snapshot:
        github_snapshot = public_document(db.candidate_github.find_one({"candidate_id": candidate_id}))

    snapshot_id = (github_snapshot or {}).get("id") or (github_snapshot or {}).get("latest_snapshot_id")
    has_verified_github = bool(github_snapshot and github_snapshot.get("verification_status") in {"verified", "success"})

    candidate = candidate_service.get_complete_candidate_profile(candidate_id) or {}
    candidate["github"] = github_snapshot if has_verified_github else None
    candidate["portfolio_evidence"] = public_document(db.portfolio_evidence_snapshots.find_one(
        {"candidate_id": candidate_id}, sort=[("verified_at", -1)]))
    candidate["linkedin_evidence"] = public_document(db.linkedin_evidence_snapshots.find_one(
        {"candidate_id": candidate_id}, sort=[("verified_at", -1)]))
    candidate["cv_records"] = [public_document(item) for item in db.cv_documents.find(
        {"candidate_id": candidate_id}, {"_id": 0, "content": 0, "raw_gemini_result": 0}).sort("uploaded_at", -1).limit(3)]
    for excluded in ("profilePhotoUrl", "photo_url", "date_of_birth", "gender", "marital_status"):
        candidate.pop(excluded, None)
    raw_evaluation, evaluation_provider = await evaluate_enterprise_job(
        public_document(job), candidate, generate_json if model else None)
    scores = enterprise_scores(raw_evaluation)
    pre_interview = raw_evaluation["candidate_score"]

    evaluation = {
        "id": identifier(),
        "application_id": application["id"],
        "candidate_id": candidate_id,
        "job_id": job["id"],
        "ai_provider": evaluation_provider,
        "model": None,
        "scoring_rules_version": SCORING_RULES_VERSION,
        "scores": scores,
        "pre_interview_score": pre_interview,
        "raw_result": raw_evaluation,
        "evaluation_version": next_version,
        "previous_evaluation_id": latest_eval.get("id") if latest_eval else None,
        "github_snapshot_id": snapshot_id if has_verified_github else None,
        "github_score": scores.get("github_evidence", {}).get("weighted_score", 0),
        "github_max_score": scores.get("github_evidence", {}).get("maximum_score", 10),
        "evidence_confidence": raw_evaluation["evidence_confidence"],
        "mandatory_requirements": raw_evaluation.get("mandatory_requirements", []),
        "skill_analysis": raw_evaluation.get("skill_analysis", []),
        "evaluated_at": utcnow().isoformat(),
        "created_at": utcnow(),
    }
    db.ai_evaluations.insert_one(evaluation)
    db.applications.update_one({"id": application["id"]}, {"$set": {
        "scores": scores,
        "score": pre_interview,
        "overallScore": pre_interview,
        "jobMatchScore": pre_interview,
        "jobMatchScoreBreakdown": raw_evaluation["score_breakdown"],
        "evidenceConfidence": raw_evaluation["evidence_confidence"],
        "enterpriseEvaluation": raw_evaluation,
        "analysis": raw_evaluation,
        "recommendedDecision": (raw_evaluation.get("recommendation") or {}).get("decision"),
        "githubEvidence": github_snapshot if has_verified_github else None,
        "githubSnapshotId": snapshot_id if has_verified_github else None,
        "evaluationVersion": next_version,
        "scoringRulesVersion": SCORING_RULES_VERSION,
        "evaluatedAt": evaluation["evaluated_at"],
    }})
    updated_app = db.applications.find_one({"id": application["id"]})
    audit(db, "application_reevaluated", user["id"], "application", application["id"], {
        "evaluation_id": evaluation["id"],
        "evaluation_version": next_version,
        "previous_evaluation_id": latest_eval.get("id") if latest_eval else None,
        "github_snapshot_id": snapshot_id,
        "pre_interview_score": pre_interview,
    })
    return public_document(updated_app)


@app.delete("/api/applications/{application_id}")
def delete_application(application_id: str, token: str):
    user = require_session(token)
    result = db.applications.delete_one({"id": application_id, "userId": user["id"]})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Application not found.")
    return {"ok": True}


@app.patch("/api/applications/{application_id}/status")
def set_application_status(application_id: str, payload: DocumentPayload, token: str):
    user = require_session(token)
    owned_jobs = [item["id"] for item in db.jobs.find({"recruiterId": user["id"]}, {"id": 1})]
    updated = db.applications.find_one_and_update({"id": application_id, "jobId": {"$in": owned_jobs}}, {"$set": {"status": payload.data.get("status", "Under review")}}, return_document=ReturnDocument.AFTER)
    if not updated:
        raise HTTPException(status_code=404, detail="Application not found.")
    return public_document(updated)


@app.delete("/api/session")
def logout(token: str):
    ensure_database()
    db.sessions.delete_one({"token": token})
    return {"ok": True}

MAX_CV_SIZE = 10 * 1024 * 1024
SUPPORTED_CV_EXTENSIONS = {".pdf", ".docx", ".txt", ".jpg", ".jpeg", ".png", ".webp"}
VISUAL_CV_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _cv_extraction_has_content(extraction):
    personal = extraction.personal_info
    personal_count = sum(bool(value) for value in (
        personal.full_name, personal.email, personal.phone, personal.location, personal.professional_title
    ))
    section_count = sum(len(items) for items in (
        extraction.technical_skills, extraction.soft_skills, extraction.experience, extraction.projects,
        extraction.education, extraction.certifications, extraction.languages,
    ))
    return personal_count >= 2 or (personal_count >= 1 and section_count >= 2) or section_count >= 4


def _normalize_cv_payload(payload):
    """Repair harmless model shape variations before strict domain validation."""
    if not isinstance(payload, dict):
        raise ValueError("Gemini CV extraction must be a JSON object.")
    normalized_payload = dict(payload)
    personal_info = normalized_payload.get("personal_info")
    github_info = normalized_payload.get("github")
    normalized_payload["personal_info"] = personal_info if isinstance(personal_info, dict) else {}
    normalized_payload["github"] = github_info if isinstance(github_info, dict) else {}
    normalized_payload["github"]["repositories"] = []
    for field in ("technical_skills", "soft_skills", "experience", "projects", "education", "certifications", "languages"):
        value = normalized_payload.get(field)
        normalized_payload[field] = value if isinstance(value, list) else []

    for field in ("technical_skills", "soft_skills"):
        normalized_payload[field] = [
            {"skill": item} if isinstance(item, str) else item
            for item in normalized_payload[field]
            if isinstance(item, (str, dict))
        ]
    normalized_payload["languages"] = [
        {"language": item} if isinstance(item, str) else item
        for item in normalized_payload["languages"]
        if isinstance(item, (str, dict))
    ]
    for field in ("experience", "projects", "education", "certifications"):
        normalized_payload[field] = [item for item in normalized_payload[field] if isinstance(item, dict)]

    def normalize_list_field(item, field, split_commas=False):
        value = item.get(field)
        if value is None:
            item[field] = []
        elif isinstance(value, str):
            item[field] = ([part.strip() for part in value.split(",") if part.strip()]
                           if split_commas else [value.strip()])
        elif not isinstance(value, list):
            item[field] = []

    for item in normalized_payload["technical_skills"]:
        if isinstance(item, dict):
            normalize_list_field(item, "evidence")
            if item.get("confidence") is None:
                item.pop("confidence", None)
    for item in normalized_payload["soft_skills"]:
        if isinstance(item, dict) and item.get("confidence") is None:
            item.pop("confidence", None)
    for item in normalized_payload["experience"]:
        for field in ("responsibilities", "achievements"):
            normalize_list_field(item, field)
        normalize_list_field(item, "technologies", split_commas=True)
        if item.get("confidence") is None:
            item.pop("confidence", None)
    for item in normalized_payload["projects"]:
        normalize_list_field(item, "technologies", split_commas=True)
        normalize_list_field(item, "achievements")
        if item.get("confidence") is None:
            item.pop("confidence", None)
    for item in normalized_payload["certifications"]:
        normalize_list_field(item, "relevant_skills", split_commas=True)
        if item.get("confidence") is None:
            item.pop("confidence", None)

    def normalize_url(value):
        if not value:
            return None
        value = re.sub(r"\s+", "", str(value or "").strip()).rstrip(".,;:)>}\]\'\"")
        if not value:
            return None
        if not re.match(r"^https?://", value, re.IGNORECASE):
            if re.match(r"^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(/.*)?$", value):
                value = f"https://{value}"
        parsed = urlparse(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else None

    if not normalized_payload.get("languages") and normalized_payload.get("human_languages"):
        normalized_payload["languages"] = normalized_payload["human_languages"]

    social_links = normalized_payload.get("social_links") or {}
    personal = normalized_payload["personal_info"]
    personal["linkedin_url"] = normalize_url(personal.get("linkedin_url") or social_links.get("linkedin_url"))
    personal["portfolio_url"] = normalize_url(personal.get("portfolio_url") or social_links.get("portfolio_url"))
    if personal.get("professional_title") and not personal.get("headline"):
        personal["headline"] = personal["professional_title"]
    elif personal.get("headline") and not personal.get("professional_title"):
        personal["professional_title"] = personal["headline"]
    github = normalized_payload["github"]
    github["profile_url"] = normalize_url(github.get("profile_url") or social_links.get("github_url"))
    for item in normalized_payload["experience"]:
        if not item.get("position") and item.get("role"):
            item["position"] = item["role"]
        if not item.get("technologies") and item.get("skills_used"):
            item["technologies"] = item["skills_used"]
    for item in normalized_payload["projects"]:
        item["github_url"] = normalize_url(item.get("github_url"))
        item["demo_url"] = normalize_url(item.get("demo_url"))
    for item in normalized_payload["certifications"]:
        item["credential_url"] = normalize_url(item.get("credential_url"))
    return normalized_payload


def _cv_extraction_text(extraction):
    """Create complete semantic text for downstream job analysis from validated extraction."""
    return json.dumps(extraction.model_dump(mode="json"), ensure_ascii=False, indent=2)


@app.post("/api/import-cv")
async def import_cv(file: UploadFile = File(...), token: str = ""):
    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in SUPPORTED_CV_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Only PDF, DOCX, TXT, JPG, PNG, and WebP CV files are supported.")

    content = await file.read(MAX_CV_SIZE + 1)
    if len(content) > MAX_CV_SIZE:
        raise HTTPException(status_code=413, detail="CV file must be 10 MB or smaller.")

    media_mime_type = VISUAL_CV_MIME_TYPES.get(extension)
    try:
        text, document_links = extract_cv_content(content, extension)
    except Exception as exc:
        if media_mime_type:
            text, document_links = "", []
        else:
            raise HTTPException(status_code=422, detail=f"Could not read this CV: {exc}") from exc

    text = text.strip()
    is_scanned_pdf = (extension == ".pdf" and len(text) < 80)
    use_visual_mode = bool(media_mime_type and (extension != ".pdf" or is_scanned_pdf))
    effective_media_data = content if use_visual_mode else None
    effective_media_mime = media_mime_type if use_visual_mode else None

    if not text and not effective_media_data:
        raise HTTPException(
            status_code=422,
            detail="No readable text or visual document was found in this CV.",
        )

    response = {"filename": file.filename, "text": text}
    if token:
        user = require_session(token)
        if user["role"] != "candidate":
            raise HTTPException(status_code=403, detail="Only candidates can upload a CV.")
        document_id = identifier()
        db.cv_documents.insert_one({"id": document_id, "candidate_id": user["id"], "file_name": file.filename,
            "content_type": file.content_type, "content": content, "uploaded_at": utcnow(), "parsing_status": "processing"})
        response["document_id"] = document_id
        linkedin_url = find_linkedin_profile_url(text, document_links)
        if linkedin_url:
            try:
                response["linkedin_verification"] = save_linkedin_profile_url(user["id"], linkedin_url, source="cv")
                response["linkedin_verification_status"] = "verified_url"
            except LinkedInVerificationError as linkedin_exc:
                response["linkedin_verification_status"] = "invalid"
                response["linkedin_verification_error"] = str(linkedin_exc)
        if model:
            prompt = f"""Extract complete, factual professional information from this CV matching the required JSON schema.
Never invent missing details. Transcribe only facts explicitly stated in the CV.

Required JSON Schema:
{EXTRACTION_SCHEMA_JSON}

CV CONTENT:
{text[:120000] if text else '[Visual document attached. Transcribe and extract all visible details according to the schema.]'}
"""
            try:
                extraction = None
                raw = None
                extraction_error = None
                for attempt in range(2):
                    attempt_prompt = prompt
                    if attempt:
                        attempt_prompt += (
                            "\nA previous pass was incomplete or did not match the schema. Re-read the entire CV from top to "
                            "bottom, audit every section, and return one complete, factual JSON object."
                        )
                    try:
                        candidate_raw = await generate_json(
                            attempt_prompt,
                            max_output_tokens=16384,
                            timeout=120,
                            media_data=effective_media_data,
                            media_mime_type=effective_media_mime,
                            system_instruction=SYSTEM_EXTRACTION_INSTRUCTION,
                        )
                        normalized_payload = normalize_cv_extraction(candidate_raw, cv_text=text)
                        candidate_extraction = CvExtraction.model_validate(normalized_payload)
                        extraction = candidate_extraction
                        raw = candidate_raw
                        break
                    except Exception as exc:
                        extraction_error = exc

                if extraction is None:
                    raise extraction_error or ValueError("Gemini returned no usable CV details.")

                save_extraction(db, user["id"], extraction, document_id, file.filename, os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
                extraction_response = extraction.model_dump(mode="json")
                extraction_response["technical_skills"] = enriched_technical_skills(extraction_response)
                response["structured_extraction"] = extraction_response
                response["text"] = _cv_extraction_text(extraction)

                # Perform candidate profile intelligence analysis, scoring, GitHub verification & analysis run recording
                intel_res = await cv_analysis_service.analyze_and_store_cv(
                    user["id"], response["text"], file.filename, document_id,
                    structured_extraction=extraction_response,
                )
                response["candidate_profile"] = intel_res.get("candidate")
                response["candidate_profile_score"] = intel_res.get("candidate_profile_score")
                response["score_breakdown"] = intel_res.get("score_breakdown")
                response["analysis_run_id"] = intel_res.get("analysis_run_id")

                if not linkedin_url and extraction.personal_info.linkedin_url:
                    linkedin_url = str(extraction.personal_info.linkedin_url)
                    try:
                        response["linkedin_verification"] = save_linkedin_profile_url(user["id"], linkedin_url, source="cv")
                        response["linkedin_verification_status"] = "verified_url"
                    except LinkedInVerificationError as linkedin_exc:
                        response["linkedin_verification_status"] = "invalid"
                        response["linkedin_verification_error"] = str(linkedin_exc)

                github_url = (str(extraction.github.profile_url) if extraction.github.profile_url else None)
                github_url = github_url or find_github_profile_url(text, document_links)
                if github_url:
                    try:
                        snapshot = await save_verified_github(user["id"], github_url)
                        response["github_verification_status"] = "verified"
                        response["github_snapshot_id"] = snapshot.get("id")
                    except GithubVerificationError as github_exc:
                        response["github_verification_status"] = "unavailable"
                        response["github_verification_error"] = str(github_exc)
                db.cv_documents.update_one({"id": document_id}, {"$set": {"parsing_status": "completed", "parsed_at": utcnow(), "raw_gemini_result": raw}})
                response["structured_status"] = "completed"
                response["extraction_mode"] = "document_vision" if media_mime_type else "document_text"
            except Exception as exc:
                error_text = str(exc).strip() or exc.__class__.__name__
                db.cv_documents.update_one({"id": document_id}, {"$set": {"parsing_status": "failed", "parsing_error": error_text, "parsed_at": utcnow()}})
                raise HTTPException(status_code=502, detail=f"Could not extract complete CV details: {error_text}") from exc
        else:
            db.cv_documents.update_one({"id": document_id}, {"$set": {"parsing_status": "pending_ai"}})
            response["structured_status"] = "pending_ai"
    return response

MAX_AI_TEXT_LENGTH = 30000


def normalize_skill(value):
    return " ".join(str(value).strip().split()).casefold()


def reconcile_skill_matches(result, job_skills, candidate_experience=0, required_experience=0):
    required = [skill.strip() for skill in job_skills.split(",") if skill.strip()]
    candidate_skills = result.get("extracted_skills", []) + result.get("matched_skills", [])
    
    # Ensure profile_details has technical_skills and soft_skills
    p_details = result.setdefault("profile_details", {})
    tech = p_details.get("technical_skills", [])
    soft = p_details.get("soft_skills", [])
    if isinstance(tech, list):
        candidate_skills.extend(tech)
    if isinstance(soft, list):
        candidate_skills.extend(soft)

    candidate_keys = {normalize_skill(skill) for skill in candidate_skills if normalize_skill(skill)}
    ai_assessments = {
        normalize_skill(item.get("skill", "")): item
        for item in result.get("skill_assessments", [])
        if isinstance(item, dict) and item.get("skill")
    }

    matched = []
    missing = []
    partial = []
    skill_breakdown = []
    partial_rules = {
        "javascript": {"react"},
    }
    seen = set()
    for skill in required:
        key = normalize_skill(skill)
        if key in seen:
            continue
        seen.add(key)
        if key in candidate_keys:
            matched.append(skill)
            credit = 1
            evidence_text = skill
        else:
            assessment = ai_assessments.get(key, {})
            status = normalize_skill(assessment.get("status", ""))
            evidence_text = str(assessment.get("evidence", "")).strip()
            if status == "full" and evidence_text:
                matched.append(skill)
                credit = 1
            elif status == "partial" and evidence_text:
                partial.append({
                    "skill": skill,
                    "evidence": evidence_text,
                    "credit": 0.5,
                })
                credit = 0.5
            else:
                evidence = partial_rules.get(key, set()) & candidate_keys
                if evidence:
                    evidence_text = sorted(evidence)[0].title()
                    partial.append({"skill": skill, "evidence": evidence_text, "credit": 0.5})
                    credit = 0.5
                else:
                    missing.append(skill)
                    credit = 0
                    evidence_text = "No supporting evidence found"
        skill_breakdown.append({
            "skill": skill,
            "status": "Full" if credit == 1 else "Partial" if credit == 0.5 else "Missing",
            "credit": credit,
            "evidence": evidence_text,
        })

    result["matched_skills"] = matched
    result["missing_skills"] = missing
    result["partial_skills"] = partial
    credited_skills = sum(item["credit"] for item in skill_breakdown)
    required_years = max(float(required_experience or 0), 0)
    candidate_years = max(float(candidate_experience or 0), 0)

    if not candidate_keys:
        skill_score = 0
        skill_points = 0
        experience_points = 0
        result["score"] = 0
    else:
        skill_score = credited_skills / len(required) if required else 1
        if required_years:
            experience_score = min(candidate_years / required_years, 1)
            skill_points = round(skill_score * 80)
            experience_points = round(experience_score * 20)
            result["score"] = skill_points + experience_points
        else:
            skill_points = round(skill_score * 100)
            experience_points = 0
            result["score"] = skill_points

    result["score_breakdown"] = {
        "skill_points": skill_points,
        "skill_max": 80 if required_years else 100,
        "experience_points": experience_points,
        "experience_max": 20 if required_years else 0,
    }
    per_skill_max = (80 if required_years else 100) / len(skill_breakdown) if skill_breakdown else 0
    for item in skill_breakdown:
        item["points"] = round(item["credit"] * per_skill_max, 1)
        item["max_points"] = round(per_skill_max, 1)
    result["skill_breakdown"] = skill_breakdown
    result["experience_match"] = {
        "candidate_years": candidate_years,
        "required_years": required_years,
        "meets_requirement": candidate_years >= required_years,
    }
    if not candidate_keys:
        result["ai_suggestion"] = "Your profile has no skills listed. Please add your technical and soft skills to your profile or upload your CV to calculate your job compatibility."
    elif not result.get("recruiter_guidance"):
        score = result["score"]
        decision = "Strong Match" if score >= 85 else "Good Match" if score >= 70 else "Moderate Match" if score >= 55 else "Weak Match"
        strengths = ", ".join(matched[:3]) or "No confirmed required skills"
        risks = ", ".join(missing[:3]) or "No major required-skill gaps"
        result["recruiter_guidance"] = {
            "recommendation": decision,
            "reason": f"{score}% overall fit based on verified skills and experience.",
            "strengths": strengths,
            "risks": risks,
            "interview_focus": f"Validate practical evidence for {strengths} and probe the candidate's plan for {risks}.",
        }
    return result


COMMON_SOFT_SKILLS = [
    "Communication", "Leadership", "Teamwork", "Collaboration", "Problem Solving",
    "Time Management", "Critical Thinking", "Adaptability", "Creativity", "Organization",
    "Work Ethic", "Interpersonal Skills", "Negotiation", "Conflict Resolution", "Agile", "Scrum"
]

COMMON_TECH_SKILLS = [
    "Python", "Java", "JavaScript", "TypeScript", "C", "C++", "C#", "PHP", "Ruby", "Go", "Rust", "Swift", "Kotlin",
    "HTML", "HTML5", "CSS", "CSS3", "React", "React.js", "Next.js", "Vue", "Vue.js", "Angular", "Node.js", "Express", "FastAPI", "Django", "Flask",
    "REST API", "REST APIs", "GraphQL", "WebSockets", "Microservices",
    "MySQL", "PostgreSQL", "MongoDB", "SQLite", "Redis", "Oracle", "SQL", "NoSQL",
    "Git", "GitHub", "GitLab", "Bitbucket", "Docker", "Kubernetes", "CI/CD", "Jenkins",
    "AWS", "Azure", "GCP", "Cloud", "Linux", "Windows", "Unix", "Bash", "Shell",
    "Postman", "VS Code", "Jira", "Figma",
    "Machine Learning", "Deep Learning", "Data Analysis", "Data Science", "Pandas", "NumPy", "TensorFlow", "PyTorch", "NLP", "AI",
    "MS Word", "Excel", "PowerPoint", "MS Office"
]

def local_analysis(job_skills, cv_text, candidate_experience=0, required_experience=0):
    required = [skill.strip() for skill in job_skills.split(",") if skill.strip()]
    cv_key = normalize_skill(cv_text)
    
    extracted_tech = []
    seen_tech = set()
    for tech in COMMON_TECH_SKILLS:
        tech_key = normalize_skill(tech)
        if tech_key in cv_key and tech_key not in seen_tech:
            seen_tech.add(tech_key)
            extracted_tech.append(tech)
            
    for skill in required:
        skill_key = normalize_skill(skill)
        if skill_key in cv_key and skill_key not in seen_tech:
            seen_tech.add(skill_key)
            extracted_tech.append(skill)

    extracted_soft = [soft for soft in COMMON_SOFT_SKILLS if normalize_skill(soft) in cv_key]
    phone_match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", cv_text)
    years_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+\s*)?years?\s+(?:of\s+)?experience", cv_text, re.I)
    profile_details = {
        "phone": phone_match.group(0).strip() if phone_match else "",
        "experience": float(years_match.group(1)) if years_match else candidate_experience,
        "technical_skills": extracted_tech,
        "soft_skills": extracted_soft,
    }
    result = reconcile_skill_matches(
        {"extracted_skills": extracted_tech + extracted_soft, "profile_details": profile_details},
        job_skills,
        candidate_experience,
        required_experience,
    )
    if result["missing_skills"]:
        missing = result["missing_skills"]
        priority = ", ".join(missing[:2])
        remaining = ", ".join(missing[2:])
        result["ai_suggestion"] = (
            f"Start with {priority}, because these are the highest-priority gaps for this role. "
            f"Complete a focused course or official tutorial, then build one small portfolio project "
            f"that uses {priority} in a realistic workflow. Add the project link and measurable results "
            f"to your CV. "
            + (f"Next, cover {remaining} and demonstrate each skill in the same project. " if remaining else "")
            + "Before applying, practise explaining your technical choices and prepare two examples of "
            "how you solved relevant problems."
        )
    else:
        result["ai_suggestion"] = (
            "Your profile covers every listed skill. Strengthen the application with one recent project, "
            "measurable outcomes, and two interview examples that demonstrate how you used these skills."
        )
    return result


async def generate_json(prompt: str, max_output_tokens: int = 8192, timeout: int = 60,
                        media_data: bytes | None = None, media_mime_type: str | None = None,
                        system_instruction: str | None = None):
    """Call Gemini REST directly without blocking FastAPI or loading an SDK."""
    current_key = api_key or os.environ.get("GEMINI_API_KEY", "")
    current_model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    return await asyncio.to_thread(
        generate_gemini_json, current_key, current_model, prompt, max_output_tokens, timeout,
        media_data, media_mime_type, system_instruction,
    )


@app.post("/api/extract-skills")
async def extract_skills(cv_text: str = Form(...)):
    if not model:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured.")

    prompt = f"""
    Extract EVERY professional skill explicitly evidenced in this CV, for any occupation or industry.
    
    UNIVERSAL CV RULE FOR SKILL CLASSIFICATION:
    1. technical_skills: Include all role-specific hard/functional skills, domain competencies, operational methods, tools, software, platforms, management disciplines, regulations, or technical processes (e.g. Financial Reporting, Patient Care, Digital Marketing, Inventory Management, Recruitment, Project Management, AutoCAD, Data Analysis, Accounting, Budgeting, HRIS, CRM, etc.).
    2. soft_skills: Include ONLY skills describing how a person communicates, collaborates, leads people, thinks, adapts, organizes themselves, builds relationships, resolves interpersonal situations, or behaves at work (e.g. Communication, Teamwork, Leadership, Adaptability, Negotiation, Conflict Resolution, Problem Solving, Critical Thinking, Time Management, Attention to Detail). Never put functional/domain competencies (like Project Management, Financial Reporting, or Patient Care) in soft_skills. When uncertain, exclude from soft_skills.

    Return ONLY valid JSON with no markdown:
    {{
        "technical_skills": ["role-specific hard or functional skill"],
        "soft_skills": ["interpersonal or behavioral skill"],
        "skills": ["all deduplicated professional skills"]
    }}

    CV:
    {cv_text[:MAX_AI_TEXT_LENGTH]}
    """
    try:
        result = await generate_json(prompt)
        raw_tech = result.get("technical_skills", [])
        raw_soft = result.get("soft_skills", [])
        
        # Enforce Universal CV Rule on soft skills
        clean_tech = list(raw_tech)
        clean_soft = []
        for s in raw_soft:
            if is_soft_skill_candidate(s):
                clean_soft.append(s)
            else:
                if s not in clean_tech:
                    clean_tech.append(s)

        all_skills = result.get("skills", clean_tech + clean_soft)
        return {
            "technical_skills": clean_tech,
            "soft_skills": clean_soft,
            "skills": all_skills,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not extract skills: {exc}") from exc

@app.post("/api/analyze-cv")
async def analyze_cv(
    job_title: str = Form(...),
    job_skills: str = Form(...),
    cv_text: str = Form(...),
    candidate_experience: float = Form(0),
    required_experience: float = Form(0),
):
    # Check if cv_text is profileText with no explicit skills listed
    if cv_text.startswith("Candidate:"):
        has_profile_skills = False
        for line in cv_text.splitlines():
            if any(line.startswith(prefix) for prefix in ("Technical Skills:", "Soft Skills:", "Skills:")):
                val = line.split(":", 1)[1].strip()
                if val:
                    has_profile_skills = True
                    break
        if not has_profile_skills:
            result = reconcile_skill_matches({"extracted_skills": [], "profile_details": {}}, job_skills, candidate_experience, required_experience)
            result["score"] = 0
            result["score_breakdown"] = {"skill_points": 0, "skill_max": 80 if required_experience else 100, "experience_points": 0, "experience_max": 20 if required_experience else 0}
            result["ai_suggestion"] = "Your profile has no skills listed. Please add your technical and soft skills to your profile or upload your CV to calculate your job compatibility score."
            return result

    if not model:
        return local_analysis(job_skills, cv_text, candidate_experience, required_experience)
        
    prompt = f"""
    You are an expert, industry-neutral Applicant Tracking System AI for technical and non-technical jobs.
    Job Title: {job_title}
    Required Skills: {job_skills}
    Required Experience: {required_experience} years
    Candidate Experience: {candidate_experience} years
    Candidate information: {cv_text[:MAX_AI_TEXT_LENGTH]}
    
    Analyze the candidate against the actual job requirements without assuming the role is a software job.
    Treat technical_skills as the existing API field for job-specific hard/functional competencies in any field, including
    HR, recruiting, management, operations, sales, customer service, finance, healthcare, education, engineering, trades,
    tools, platforms, processes, methodologies, regulations, and software. Extract only genuine professional skills and
    normalize equivalent spellings. Never include names, emails, phone numbers,
    employers, schools, job titles, or locations in extracted_skills. Remove duplicate skills.
    In profile_details, extract every evidenced functional/hard skill into technical_skills and every evidenced interpersonal
    skill into soft_skills. Do not restrict either list to job requirements and do not favor software terminology.
    Evaluate every required skill as Full, Partial, or Missing. Full requires explicit evidence
    or a clearly equivalent skill. Partial means transferable or prerequisite knowledge and
    always receives 0.5 credit. Missing receives 0 credit. Include a short evidence phrase from
    the candidate information; never infer a skill from job title alone.
    For ai_suggestion, provide concise, personalized career guidance based specifically on the
    partial and missing skills. Prioritize what to learn first, recommend one practical portfolio project,
    explain what evidence to add to the CV, and give an interview-preparation next step. Do not
    give generic encouragement, invent candidate experience, or recommend unrelated skills.
    For recruiter_guidance, act as a professional hiring advisor and give a clear evidence-based
    match band of Strong Match, Good Match, Moderate Match, or Weak Match. Explain the evidence without making a hiring decision,
    the strongest job-related evidence, hiring risks from missing or partial skills, and the exact
    next interview or practical-assessment step. Do not use protected personal characteristics,
    make the final employment decision for the recruiter, or invent evidence.
    Also extract profile_details only when explicitly supported by the CV. Use an empty string
    for unavailable text fields and 0 for unavailable experience. The bio must be a factual
    professional summary of no more than 60 words. Never include email, name, or sensitive data.
    Strictly return ONLY a valid JSON object in the following format, with no markdown formatting or backticks:
    {{
        "score": 85,
        "matched_skills": ["skill1", "skill2"],
        "missing_skills": ["skill3", "skill4"],
        "extracted_skills": ["all normalized candidate skills"],
        "skill_assessments": [
            {{"skill": "each required skill", "status": "Full|Partial|Missing", "evidence": "short factual reason"}}
        ],
        "profile_details": {{
            "headline": "professional headline",
            "phone": "phone number",
            "location": "city or region",
            "bio": "concise factual professional summary",
            "experience": 2,
            "technical_skills": ["Python", "Java", "JavaScript", "HTML", "CSS", "React", "FastAPI", "REST APIs", "MySQL", "SQL", "Git", "GitHub", "Postman", "VS Code", "Windows", "Linux", "AWS", "Machine Learning", "Data Analysis", "MS Word", "Excel", "PowerPoint"],
            "soft_skills": ["Communication", "Problem Solving", "Team Leadership"]
        }},
        "ai_suggestion": "Candidate has strong basics but needs to learn...",
        "recruiter_guidance": {{
            "recommendation": "Strong Match|Good Match|Moderate Match|Weak Match",
            "reason": "evidence-based hiring rationale",
            "strengths": "strongest relevant evidence",
            "risks": "missing or partial skill risks",
            "interview_focus": "what to validate next"
        }}
    }}
    """
    
    try:
        result = await generate_json(prompt)
        return reconcile_skill_matches(result, job_skills, candidate_experience, required_experience)
    except Exception as e:
        return local_analysis(job_skills, cv_text, candidate_experience, required_experience)


@app.get("/api/candidates/{candidate_id}/profile-intelligence")
def get_candidate_profile_intelligence(candidate_id: str, token: str):
    require_session(token)
    profile = candidate_service.get_complete_candidate_profile(candidate_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Candidate profile not found.")
    
    history = candidate_service.get_candidate_analysis_history(candidate_id)
    return public_document({
        **profile,
        "analysis_history": [public_document(h) for h in history]
    })


from assessment_routes import build_assessment_router
app.include_router(build_assessment_router(require_session))

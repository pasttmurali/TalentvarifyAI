# Full Application Audit Report

**Audit mode:** Read-only. No application source code, configuration, database, environment variables, commits, or schema changes were made during the audit.

## 1. Executive Summary

- **Overall Application Health:** 52/100
- **Security Health:** 38/100
- **Code Quality:** 58/100
- **UX Quality:** 64/100
- **Data Integrity:** 55/100
- **Candidate Scoring Reliability:** 48/100
- **Production Readiness:** 35/100

The application is functional as a prototype. The frontend production build, backend import, and Node syntax check passed. However, it is not production-ready because of authorization gaps, bearer tokens in URLs, non-expiring sessions, inconsistent scoring systems, weak test coverage, and evidence-display inconsistencies.

## 2. System Architecture

The application uses React 18, Vite, Tailwind CSS, FastAPI, MongoDB/PyMongo, Gemini REST API, GitHub REST API, portfolio HTML inspection, and optional LinkedIn OAuth/API verification.

Main flow:

1. The user signs in.
2. The frontend stores an opaque session token in `localStorage`.
3. API requests send the token as a query parameter.
4. Candidate profile and evidence data are stored in MongoDB.
5. CV, GitHub, portfolio, and LinkedIn evidence are collected.
6. Job evaluation uses deterministic logic or Gemini.
7. Applications, scores, snapshots, and recruiter decisions are stored.

Architectural concerns:

- Multiple legacy and enterprise scoring engines coexist.
- `backend/main.py` contains authentication, uploads, evidence, scoring, and application orchestration.
- Candidate data is duplicated across `users`, `candidate_profiles`, `candidates`, and structured collections.
- MongoDB writes across multiple collections are not transactional.
- Many endpoints use unrestricted dictionaries instead of strict request models.
- Root documentation and the `documents/` documentation tree can diverge.

## 3. What Works Well

- Passwords use PBKDF2-HMAC-SHA256 with random salts.
- Application ownership checks exist in several routes.
- GitHub snapshots are intended to be immutable.
- CV uploads have a 10 MB limit and support PDF, DOCX, and TXT.
- Gemini results are normalized and score categories are clamped.
- Protected characteristics are excluded from several evaluation paths.
- GitHub API calls use timeouts and cached evidence fallback.
- Portfolio redirect destinations are checked for private-network addresses.
- Candidate photo visibility is explicitly controlled.
- Frontend build passed successfully.
- Backend import passed successfully.
- No source code was modified during the audit.

## 4. Critical Issues

No confirmed critical issue was identified through static review. Runtime exploitation was not performed.

## 5. High Priority Issues

### H-01: Session tokens are exposed in URLs and never expire

- **Area:** Authentication/security
- **Files:** `backend/main.py`, `frontend/src/App.jsx`
- **Evidence:** Protected routes receive `token` through query parameters. `require_session()` checks token existence but does not enforce expiry, despite sessions storing `createdAt`.
- **Impact:** Tokens may leak through browser history, proxy logs, referrers, analytics, screenshots, or support logs. Stolen tokens remain valid indefinitely.
- **Recommendation:** Use secure HTTP-only cookies or authorization headers, enforce expiration, and revoke sessions explicitly.
- **Code changed:** No.

### H-02: Candidate profile intelligence has an IDOR risk

- **Area:** Authorization/privacy
- **File:** `backend/main.py`, `get_candidate_profile_intelligence`
- **Evidence:** The route authenticates the caller but does not verify candidate ownership or recruiter job/application access before reading a candidate ID.
- **Impact:** An authenticated user who knows a candidate ID may retrieve profile intelligence and analysis history.
- **Recommendation:** Enforce candidate self-access or recruiter application/job ownership.
- **Code changed:** No.

### H-03: Recruiter evidence routes lack job-scope authorization

- **Area:** Authorization/privacy
- **File:** `backend/assessment_routes.py`
- **Routes:** GitHub snapshots, snapshot comparison, and skills matrix.
- **Evidence:** Candidate self-access is checked, but recruiters are not verified against a related job or application.
- **Impact:** Cross-recruiter candidate evidence disclosure.
- **Recommendation:** Require a valid recruiter-to-candidate job/application relationship.
- **Code changed:** No.

### H-04: Configured scoring weights do not control application scores

- **Area:** Scoring correctness
- **Files:** `backend/main.py`, `backend/services/enterprise_job_evaluation_service.py`
- **Evidence:** Recruiter-configured weights are stored, but enterprise application evaluation uses fixed category maxima.
- **Impact:** Recruiters may believe custom scoring is active while submitted scores use fixed weights.
- **Recommendation:** Pass validated job weights into the authoritative evaluator and persist the exact scoring version.
- **Code changed:** No.

### H-05: Multiple scoring systems produce different scores

- **Area:** Product correctness
- **Files:** `backend/services/scoring_service.py`, `backend/services/job_match_service.py`, `backend/services/enterprise_job_evaluation_service.py`
- **Evidence:** Candidate profile score, legacy job-match score, and enterprise job score use different categories, weights, and maximums.
- **Impact:** Preview, application, recruiter review, and assessment scores can disagree.
- **Recommendation:** Define one authoritative score contract and clearly separate profile quality, job match, and post-interview scores.
- **Code changed:** No.

### H-06: Assessment aggregation does not align with enterprise score keys

- **Area:** Scoring/data consistency
- **Files:** `backend/assessment_routes.py`, `backend/enterprise_evaluation.py`
- **Evidence:** `profile_totals()` expects keys such as `experience`, while enterprise scoring produces `relevant_experience`. Some categories are therefore omitted or treated as zero.
- **Impact:** Recruiter assessment displays can differ from stored application scores.
- **Recommendation:** Align category names and use one normalized score schema.
- **Code changed:** No.

### H-07: Known demo accounts are seeded on startup

- **Area:** Security/deployment
- **File:** `backend/main.py`
- **Evidence:** Demo candidate and recruiter accounts are created with publicly documented credentials.
- **Impact:** Exposed environments can be accessed trivially.
- **Recommendation:** Gate demo seeding behind explicit development configuration.
- **Code changed:** No.

### H-08: Recruiter skills matrix expects fields the API does not provide

- **Area:** Frontend/backend contract
- **Files:** `backend/assessment_routes.py`, `frontend/src/components/RecruiterDashboard.jsx`
- **Evidence:** API returns fields such as `github_status` and `github_details`, while UI reads `verification_status`, `repositories`, `notes`, and `evidence`.
- **Impact:** Status badges, repository details, and drill-down actions may be blank or unavailable.
- **Recommendation:** Define and validate one shared matrix response contract.
- **Code changed:** No.

## 6. Medium Priority Issues

- `/api/extract-skills`, `/api/analyze-cv`, and unauthenticated CV parsing are resource-consuming routes without authentication or rate limiting.
- CV validation relies mainly on file extension; MIME/content signature validation is incomplete.
- Parser and AI error details may be exposed to users.
- CV extraction and application submission write across multiple MongoDB collections without transactions.
- Portfolio SSRF protection has a DNS time-of-check/time-of-use risk.
- LinkedIn URL validation and official LinkedIn verification may be confused by users because both use verification language.
- Profile save is not consistently awaited before success messages are shown.
- Recruiter assessment form submissions do not consistently catch and display API failures.
- `evaluate_projects()` mutates project dictionaries while scoring.
- Candidate evaluation prompts may include unnecessary personal information such as email and phone.
- Password reset, email verification, rate limiting, and account lockout are not implemented.

## 7. Low Priority Issues

- The login page contains a non-functional `Forgot password?` button.
- Some profile-loading errors are silently swallowed.
- Public job-loading failures become an empty job list without a visible error.
- Several modals lack focus trapping, Escape handling, and focus restoration.
- Some form controls lack explicit label relationships.
- Dense evidence tables are difficult to use on small screens.
- `documents/` duplicates the root documentation tree.
- Backend dependencies are not pinned.
- No deployment configuration, container definition, CI workflow, or production server configuration was found.
- Generated frontend build output appears in the workspace while the ignore rule targets a different path.

## 8. Candidate Scoring Audit

- **Mathematically correct:** The enterprise evaluator clamps category scores and recomputes a total from nine categories whose fixed maxima sum to 100.
- **Job-specific:** Yes, the enterprise evaluator uses job skills, experience, description, and candidate evidence.
- **Evidence-aware:** Partially. GitHub, projects, portfolio, and CV are considered, but evidence semantics differ by scoring path.
- **Reproducible:** Partially. Deterministic fallback is reproducible, but Gemini output, multiple evaluators, and assessment-key mismatch reduce reproducibility.
- **Backend-authoritative:** The application score is calculated in the backend, but recruiter-configured weights are not applied by the enterprise evaluator.
- **Fairness:** The design separates confidence from suitability in several prompts, but unavailable GitHub evidence can still reduce the deterministic GitHub score to zero.

## 9. Candidate Evidence Audit

- **CV:** PDF, DOCX, and TXT supported; extracted data is schema-validated but not independently fact-verified.
- **Manual skills:** Source metadata exists, but aliases such as `JS`, `Javascript`, and `JavaScript` are not normalized consistently.
- **Experience:** Dates and technologies are stored; malformed dates can silently reduce duration evidence.
- **Projects:** Stored and optionally linked to GitHub; matching is heuristic.
- **Education:** Stored, but relevance checks use broad substring matching.
- **Languages:** Human and programming language records are supported.
- **Certifications:** Stored, but credential URLs are not independently verified.
- **GitHub:** Strongest external integration with repository evidence and snapshots.
- **Portfolio:** Public HTML is inspected and claimed skills are matched; this does not prove authorship or proficiency.
- **LinkedIn:** Public URL validation and optional OAuth/API verification exist. LinkedIn skills and certifications are not generally supplied by the API.

## 10. Security Findings

Positive controls:

- Salted password hashing.
- Backend-only database and Gemini credentials.
- Configurable CORS.
- Explicit candidate photo visibility.
- Removal of password hashes and MongoDB IDs from responses.

Main risks:

- Query-string bearer tokens.
- Non-expiring sessions.
- Known demo credentials.
- Candidate profile intelligence IDOR.
- Recruiter evidence authorization gaps.
- No rate limiting.
- Public static upload path.
- CV parser and portfolio SSRF exposure.
- Inconsistent request validation.
- Potential unnecessary PII sent to Gemini.

## 11. Database Findings

- Major collections and application uniqueness indexes exist.
- GitHub snapshots and analysis runs are inserted as historical records.
- `candidate_job_matches` upserts the latest match and is not a complete immutable history.
- Candidate data is duplicated across multiple collections.
- Multi-collection writes are not transactional.
- No migration/versioning framework was found.
- No retention, deletion, backup, or disaster-recovery policy was found.

## 12. Frontend / UX Findings

Strengths:

- Candidate and recruiter flows are separated.
- Major upload, GitHub, and AI actions show loading states.
- Empty states and error banners exist in many sections.
- Job search and recruiter filtering are implemented.
- Score explanation controls are useful.

Concerns:

- Non-functional password recovery action.
- Silent API failures in several profile/loading paths.
- Success messages can appear before persistence completes.
- Matrix API/UI field mismatch.
- Dense mobile tables.
- Incomplete keyboard and screen-reader behavior for modals and icon actions.
- Ambiguous distinction between confidence percentage, match percentage, and proficiency.

## 13. Backend / API Findings

- Main routes exist for authentication, profiles, jobs, applications, evidence, scoring, assessments, and decisions.
- Application ownership checks are stronger than candidate evidence checks.
- Request validation is inconsistent because many endpoints accept unrestricted dictionaries.
- Response schemas are not consistently defined.
- Error behavior varies between synchronous and asynchronous paths.
- Business logic is concentrated in `main.py`.

## 14. AI / Gemini Findings

- Gemini calls use timeout handling and JSON response configuration.
- Enterprise evaluation falls back to deterministic scoring on failure.
- CV extraction uses Pydantic validation in the primary path.
- Prompts instruct the model not to invent facts.

Risks:

- Validated AI output becomes stored profile data without factual verification.
- Legacy analysis endpoints silently fall back or expose errors inconsistently.
- Raw CV text is sent to Gemini and stored in MongoDB.
- Prompt-injection protections are not evident.
- No per-user AI quota or budget control was found.

## 15. GitHub Integration Findings

Strengths:

- Uses the GitHub API.
- Supports anonymous and token-authenticated access.
- Filters forks and archived repositories.
- Detects languages, technologies, README, tests, Docker, and workflow evidence.
- Preserves historical snapshots and cached evidence after rate limits.

Risks:

- GitHub quality score, profile score, and enterprise GitHub score use different scales.
- Percentages such as `98%` represent evidence confidence, not skill proficiency.
- Missing GitHub evidence can become a zero category score.
- Repository activity is only a proxy for technical evidence.

## 16. LinkedIn Integration Findings

- Public profile/post URL validation exists.
- Optional OAuth integration exists in `backend/linkedin_evidence.py`.
- Client secrets remain backend-side.
- OAuth state uses a TTL index.
- Access tokens are not persisted.

Risks:

- API scopes and tier availability affect returned evidence.
- URL validation must not be represented as identity, workplace, education, or skills verification.
- LinkedIn skills and certifications are not generally available through the implemented API.

## 17. Performance Findings

- GitHub verification performs multiple external API requests per repository.
- CV import can trigger Gemini extraction, profile intelligence, and GitHub verification in one request.
- Recruiter expansion performs separate evidence requests.
- Candidate profile loading performs separate structured-profile and matrix requests.
- No broad caching or request deduplication was found.
- Large evidence arrays can increase response size.
- No load test or production performance evidence exists.

## 18. Missing Tests

No repository-level test files or test runner configuration were found.

Missing tests include:

- Authentication and session expiry.
- Candidate/recruiter authorization and IDOR prevention.
- CV file validation and parsing.
- Gemini malformed output and fallback behavior.
- Deterministic scoring and score weights.
- Mandatory requirement statuses.
- GitHub rate limits and API failures.
- Portfolio SSRF protection.
- LinkedIn OAuth state and callback behavior.
- Skills matrix API/frontend contract.
- Duplicate applications and concurrent writes.
- Historical snapshot immutability.
- Recruiter overrides and final decisions.
- Responsive, keyboard, and screen-reader behavior.
- End-to-end candidate and recruiter workflows.

## 19. Edge Cases

- No GitHub: evaluation generally continues, but GitHub score may become zero.
- No LinkedIn: profile flow generally continues without LinkedIn evidence.
- No CV: profile-only evaluation can work when skills exist.
- Empty CV: readable-text validation fails.
- Corrupt CV: parser failure is recorded, but parser details may leak.
- 100+ skills: UI initially displays 32 with `See more`.
- Duplicate skills: case-insensitive removal is partial; aliases remain inconsistent.
- No experience: scoring and mandatory eligibility may differ between engines.
- Zero GitHub repositories: profile can be verified while quality is zero.
- GitHub rate limit: cached evidence may be reused for the same profile.
- Gemini unavailable: deterministic fallback is used.
- Invalid JSON: enterprise evaluation falls back.
- Duplicate application: route checks and database uniqueness reduce normal duplicates.
- Concurrent writes: partial multi-collection state remains possible.
- Multiple recruiters: current evidence routes may expose candidates across recruiter boundaries.
- Very large anonymous analysis input: rate and body-size controls are insufficient.

## 20. Production Readiness

**Classification: NEEDS IMPROVEMENT**

The application is suitable for demonstration and controlled development. It should not be used for real hiring decisions until authorization, session security, scoring consistency, evidence contracts, automated testing, privacy controls, backups, monitoring, and deployment hardening are addressed.

## 21. Prioritized Action Plan

### P0 — Fix immediately

1. Fix candidate profile intelligence authorization.
2. Enforce recruiter scope on GitHub snapshots, comparisons, and skills matrix routes.
3. Move tokens out of query strings and enforce session expiration.
4. Disable demo account seeding outside development.

### P1 — Fix before production

1. Unify enterprise, legacy, profile, and assessment scoring.
2. Apply recruiter-configured weights to authoritative evaluation.
3. Align the skills matrix API and frontend contract.
4. Add authentication, rate limiting, and body-size limits to expensive endpoints.
5. Add strict request and response schemas.
6. Add transaction or compensation handling for multi-collection writes.
7. Add upload signature validation and privacy controls.
8. Add authorization, scoring, API, and end-to-end tests.

### P2 — Important improvement

1. Handle all frontend async errors consistently.
2. Improve modal keyboard and screen-reader behavior.
3. Clarify URL validation versus identity/employment verification.
4. Improve skill aliases and normalization.
5. Add migration/versioning and retention policies.
6. Pin backend dependencies.
7. Add monitoring, backups, and deployment configuration.

### P3 — Future improvement

1. Add load testing and external API caching.
2. Add recruiter pagination and candidate search.
3. Add password reset and email verification.
4. Add formal consent and privacy-management workflows.
5. Add visual regression and mobile end-to-end testing.

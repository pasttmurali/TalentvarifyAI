# 🎨 TalentVerifyAI - Frontend Architecture & Component Documentation

This guide provides a complete technical explanation of the **Frontend Layer** of TalentVerifyAI, detailing its technology stack, component hierarchy, user state management, and API integration workflow.

---

## 🏗️ 1. Overview & Technology Stack

The TalentVerifyAI frontend is built as a Single Page Application (SPA) designed for ultra-fast navigation, responsive UI design, and seamless user experiences.

- **Core Framework**: React 18 (Functional Components & React Hooks)
- **Build Tool & Dev Server**: Vite 5
- **Styling Framework**: TailwindCSS v3 + Lucide Icons (`lucide-react`)
- **API Transport Client**: Native `fetch` with GZip JSON parsing and error handling helper (`api.js`)

---

## 📂 2. File & Component Structure

```text
frontend/
├── index.html                  # Main HTML entry point
├── package.json                # Dependencies and script definitions
├── vite.config.js              # Vite configuration
└── src/
    ├── main.jsx                # Application root mounting & Error Boundary wrapper
    ├── App.jsx                 # Main Router & Global Application State Manager
    ├── index.css               # Global TailwindCSS styling rules
    ├── services/
    │   └── api.js              # REST API HTTP Client helper
    └── components/
        ├── BrandLogo.jsx           # Reusable TalentVerifyAI Brand Logo
        ├── LandingPage.jsx         # Public Home Page for visitors
        ├── LoginPage.jsx           # Auth Modal (Sign In / Account Registration)
        ├── CandidateDashboard.jsx  # Candidate Dashboard (Jobs, Match Scores, Applications)
        ├── RecruiterDashboard.jsx  # Recruiter Dashboard (Job Posts, AI Evidence Review)
        └── ProfilePage.jsx         # Candidate Profile & CV Verification Workspace
```

---

## 🔀 3. Application Lifecycle & Routing Flow (`App.jsx`)

The frontend relies on a state-driven router architecture managed within `App.jsx`.

1. **Token Check**: Reads session token from `localStorage`.
2. **Data Fetching**: If token exists, fetches `/api/app-data`; otherwise fetches `/api/public/jobs`.
3. **Role Routing**: Renders `RecruiterDashboard` for recruiters and `CandidateDashboard` or `ProfilePage` for candidates.

---

## 🧩 4. Detailed Component Breakdowns

### 1. `main.jsx` (Application Entry Point)
- Wraps the entire application in a React `ErrorBoundary` class component.
- If a JavaScript runtime crash occurs, it displays a user-friendly recovery page instead of a blank white screen.

### 2. `App.jsx` (Global State Manager)
- Holds central state: `user`, `jobs`, `applications`, `page` (`'dashboard'` | `'profile'`).
- Handles authentication token persistence using browser `localStorage`.
- Passes update handler functions (`saveProfile`, `addJob`, `addApplication`, `logout`) down to child components.

### 3. `LandingPage.jsx` (Public Portal)
- Displayed when no user is logged in.
- Shows public job openings, platform features, and buttons to trigger Sign In / Register modals.

### 4. `LoginPage.jsx` (Auth Workspace)
- Switchable modal component for **Sign In** and **New Account Registration**.
- Supports candidate and recruiter role selection upon signup.

### 5. `CandidateDashboard.jsx` (Candidate View)
- Displays available job listings, candidate job match scores, missing skill gaps, applied jobs status, and direct application submit action.

### 6. `RecruiterDashboard.jsx` (Recruiter View)
- Enables recruiters to post new jobs, customize category scoring weights (CV, GitHub, Experience, Education), view incoming applications, inspect candidate GitHub/LinkedIn verification evidence, and update hiring statuses.

### 7. `ProfilePage.jsx` (Candidate Verification Workspace)
- Drag-and-drop CV uploader for auto-extracting skills via Gemini AI.
- Displays interactive Skills Matrix, verified GitHub repositories, LinkedIn evidence, and manual profile editing options.

---

## 📡 5. API Integration Layer (`api.js`)

All communication with the FastAPI backend passes through `services/api.js`:
- Automatically sets `Content-Type: application/json` for POST/PATCH requests.
- Parses compressed JSON responses.
- Catches network connection failures and formats validation error messages cleanly for the UI.

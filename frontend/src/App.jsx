import React, { useEffect, useState } from 'react';
import { LayoutDashboard, Loader2, LogOut, User } from 'lucide-react';
import CandidateDashboard from './components/CandidateDashboard';
import RecruiterDashboard from './components/RecruiterDashboard';
import LoginPage from './components/LoginPage';
import ProfilePage from './components/ProfilePage';
import BrandLogo from './components/BrandLogo';
import LandingPage from './components/LandingPage';
import { apiRequest } from './services/api';

// Key name used to persist authentication session token in browser LocalStorage
const TOKEN_KEY = 'tv_session_token';

// Helper function to safely read authentication token from browser LocalStorage
function readToken() {
  try { return window.localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

// Helper function to store or remove authentication token in browser LocalStorage
function storeToken(token) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch { /* Keeps app working even if browser cookies/storage are restricted */ }
}

/**
 * Root React Application Component
 * 
 * Manages central application state:
 * 1. User session & authentication token
 * 2. Active view page ('dashboard' or 'profile')
 * 3. Shared state for jobs list and candidate applications list
 */
export default function App() {
  // Central Application React States
  const [token, setToken] = useState(readToken); // Auth session token
  const [user, setUser] = useState(null);       // Current logged-in user object ({ id, name, role, email, ... })
  const [jobs, setJobs] = useState([]);         // List of job postings
  const [applications, setApplications] = useState([]); // List of job applications
  const [page, setPage] = useState('dashboard'); // Navigation view state ('dashboard' | 'profile')
  const [loading, setLoading] = useState(Boolean(token)); // Spinner loading state
  const [startupError, setStartupError] = useState(''); // Global startup error message
  const [authMode, setAuthMode] = useState(null); // Auth modal mode ('login' | 'register' | null)

  // STEP 1: Fetch initial application data on component mount or token state change
  useEffect(() => {
    // If not logged in, fetch public job listings for landing page view
    if (!token) {
      setLoading(true);
      apiRequest('/api/public/jobs')
        .then((data) => setJobs(data || []))
        .catch(() => setJobs([]))
        .finally(() => setLoading(false));
      return;
    }

    // If logged in, fetch complete app data (user account, jobs, applications)
    apiRequest(`/api/app-data?token=${encodeURIComponent(token)}`)
      .then((data) => {
        setUser(data.user);
        setJobs(data.jobs || []);
        setApplications(data.applications || []);
      })
      .catch((error) => {
        // Invalid or expired token: clear session and reset to unauthenticated state
        storeToken('');
        setToken('');
        setUser(null);
        setStartupError(error.message);
      })
      .finally(() => setLoading(false));
  }, [token]);

  // STEP 2: Handle user Login and Registration requests
  const handleAuth = async (credentials) => {
    try {
      const result = await apiRequest('/api/auth', {
        method: 'POST',
        body: JSON.stringify(credentials),
      });
      storeToken(result.token);
      setStartupError('');
      setToken(result.token);
      setUser(result.user);
      return {};
    } catch (error) {
      return { error: error.message };
    }
  };

  // STEP 3: API helper functions for mutating state (Profile, Jobs, Applications)
  const saveProfile = async (profile) => {
    const updated = await apiRequest(`/api/profile?token=${encodeURIComponent(token)}`, {
      method: 'PATCH',
      body: JSON.stringify({ data: profile }),
    });
    setUser(updated);
    return updated;
  };

  const addJob = async (job) => {
    const created = await apiRequest(`/api/jobs?token=${encodeURIComponent(token)}`, {
      method: 'POST',
      body: JSON.stringify({ data: job }),
    });
    setJobs((current) => [created, ...current]);
  };

  const updateJob = async (jobId, changes) => {
    const updated = await apiRequest(`/api/jobs/${jobId}?token=${encodeURIComponent(token)}`, {
      method: 'PATCH',
      body: JSON.stringify({ data: changes }),
    });
    setJobs((current) => current.map((job) => (job.id === jobId ? updated : job)));
  };

  const addApplication = async (application) => {
    const created = await apiRequest(`/api/applications?token=${encodeURIComponent(token)}`, {
      method: 'POST',
      body: JSON.stringify({ data: application }),
    });
    setApplications((current) => [created, ...current]);
    return created;
  };

  const withdrawApplication = async (applicationId) => {
    await apiRequest(`/api/applications/${applicationId}?token=${encodeURIComponent(token)}`, {
      method: 'DELETE',
    });
    setApplications((current) => current.filter((item) => item.id !== applicationId));
  };

  const updateApplicationStatus = async (applicationId, status) => {
    const updated = await apiRequest(`/api/applications/${applicationId}/status?token=${encodeURIComponent(token)}`, {
      method: 'PATCH',
      body: JSON.stringify({ data: { status } }),
    });
    setApplications((current) => current.map((item) => (item.id === applicationId ? updated : item)));
  };

  // STEP 4: Handle User Logout
  const logout = async () => {
    try {
      await apiRequest(`/api/session?token=${encodeURIComponent(token)}`, { method: 'DELETE' });
    } catch { /* Always clear local state even if network request fails */ }
    storeToken('');
    setToken('');
    setUser(null);
    setJobs([]);
    setApplications([]);
    setPage('dashboard');
  };

  // STEP 5: Render Views based on Auth State
  // View 1: Auth Login/Register Screen
  if (!user && authMode) {
    return (
      <LoginPage
        onLogin={handleAuth}
        initialError={startupError}
        initialMode={authMode}
        onBack={() => { setAuthMode(null); setStartupError(''); }}
      />
    );
  }

  // View 2: Public Landing Page for unauthenticated visitors
  if (!user) {
    return (
      <LandingPage
        jobs={jobs}
        loading={loading}
        onSignIn={() => setAuthMode('login')}
        onCreateAccount={() => setAuthMode('register')}
        onApply={() => setAuthMode('login')}
      />
    );
  }

  // View 3: App Loading Spinner while fetching initial profile
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-white">
        <Loader2 className="animate-spin" size={32} />
      </div>
    );
  }

  // View 4: Main Authenticated Dashboard Shell (Header + Navigation + Active Page View)
  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 font-sans flex flex-col">
      <header className="sticky top-0 z-40 flex items-center justify-between gap-2 border-b border-slate-200 bg-white/95 px-3 py-3 shadow-sm backdrop-blur sm:px-6">
        <BrandLogo />
        <div className="flex shrink-0 items-center gap-1 sm:gap-3">
          <button
            onClick={() => setPage('dashboard')}
            className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold ${
              page === 'dashboard' ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <LayoutDashboard size={17} />
            <span className="hidden md:inline">Home</span>
          </button>
          <button
            onClick={() => setPage('profile')}
            className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold ${
              page === 'profile' ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <User size={17} />
            <span className="hidden md:inline">Profile</span>
          </button>
          <button
            onClick={logout}
            className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
          >
            <LogOut size={17} />
            <span className="hidden sm:inline">Logout</span>
          </button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-3 py-4 sm:px-6 sm:py-6 lg:px-8">
        {page === 'profile' ? (
          <ProfilePage user={user} token={token} onSave={saveProfile} />
        ) : user.role === 'recruiter' ? (
          <RecruiterDashboard
            user={user}
            token={token}
            jobs={jobs.filter((job) => job.recruiterId === user.id)}
            applications={applications}
            onAddJob={addJob}
            onUpdateJob={updateJob}
            onUpdateApplicationStatus={updateApplicationStatus}
          />
        ) : (
          <CandidateDashboard
            jobs={jobs}
            user={user}
            token={token}
            applications={applications.filter((item) => !['Withdrawn', 'Application Withdrawn'].includes(item.status))}
            onSaveProfile={saveProfile}
            onApply={addApplication}
            onWithdraw={withdrawApplication}
            onNavigateProfile={() => setPage('profile')}
          />
        )}
      </main>
    </div>
  );
}

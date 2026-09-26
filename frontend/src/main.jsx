// STEP 1: Import React core libraries
// - React: Core React library for components and lifecycle
// - ReactDOM: Provides DOM-specific methods to mount React components into the HTML page
import React from 'react'
import ReactDOM from 'react-dom/client'

// STEP 2: Import the primary App component and global styles
// - App.jsx: The central component holding all page routes and global user state
// - index.css: Imports TailwindCSS base styles and utility classes
import App from './App.jsx'
import './index.css'

// STEP 3: Define an ErrorBoundary component
// WHY THIS STEP: If any unexpected JavaScript error crashes the React component tree,
// this ErrorBoundary catches it gracefully and shows a friendly recovery screen
// instead of letting the browser display a blank white screen!
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    // Initialize error state to null (no error initially)
    this.state = { error: null }
  }

  // Lifecycle method called automatically by React when a child component throws an error
  static getDerivedStateFromError(error) {
    // Update state so the next render shows the fallback UI
    return { error }
  }

  render() {
    // If no error occurred, render the child component normally (the App component)
    if (!this.state.error) return this.props.children

    // Fallback UI shown only when an error occurs
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 p-6">
        <section className="w-full max-w-lg rounded-2xl bg-white p-8 text-center shadow-2xl">
          <h1 className="text-2xl font-bold text-slate-900">TalentVerifyAI could not start</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Clear this site's browser data, reload the page, and restart the app with <code className="rounded bg-slate-100 px-1.5 py-1">npm.cmd run dev</code> if needed.
          </p>
          <button type="button" onClick={() => window.location.reload()} className="mt-6 rounded-xl bg-blue-600 px-5 py-3 font-semibold text-white hover:bg-blue-700">
            Reload page
          </button>
          <details className="mt-6 text-left text-xs text-slate-500">
            <summary className="cursor-pointer font-semibold">Technical details</summary>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-100 p-3">
              {String(this.state.error?.message || this.state.error)}
            </pre>
          </details>
        </section>
      </main>
    )
  }
}

// STEP 4: Locate the root DOM element in index.html (<div id="root"></div>)
// WHY THIS STEP: React needs an HTML container element to render into
const root = document.getElementById('root')
if (!root) throw new Error('The application root element is missing.')

// STEP 5: Mount and render the React application
// - createRoot: Uses React 18 Concurrent Root API for fast rendering
// - StrictMode: Development tool that highlights potential bugs and deprecated practices
// - ErrorBoundary: Wraps App to protect against unhandled UI crashes
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)


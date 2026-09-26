// =========================================================================================
// FILE: tailwind.config.js
// PURPOSE: Configuration for Tailwind CSS utility framework.
// =========================================================================================

/** @type {import('tailwindcss').Config} */
export default {
  // ---------------------------------------------------------------------------------------
  // STEP 1: CONTENT SCANNING PATHS
  // WHY THIS STEP:
  // - Tells Tailwind which files to inspect for utility classes (e.g. bg-blue-600, flex, p-4).
  // - Unused styles are automatically purged during production build (tree-shaking),
  //   keeping the final CSS bundle size ultra-small and fast to load.
  // ---------------------------------------------------------------------------------------
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],

  // ---------------------------------------------------------------------------------------
  // STEP 2: THEME EXTENSION
  // WHY THIS STEP:
  // - Allows adding custom colors, fonts, spacing, or breakpoints without overriding defaults.
  // ---------------------------------------------------------------------------------------
  theme: {
    extend: {},
  },

  // ---------------------------------------------------------------------------------------
  // STEP 3: PLUGINS
  // WHY THIS STEP:
  // - Enables optional official plugins (such as @tailwindcss/forms, typography) if needed.
  // ---------------------------------------------------------------------------------------
  plugins: [],
}
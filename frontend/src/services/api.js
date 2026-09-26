/**
 * Central API Client Module
 * 
 * Provides unified helper functions for making HTTP REST calls to the FastAPI backend.
 * Handles automatic headers, JSON parsing, error extraction, and FormData uploads.
 */

// STEP 1: Determine the Backend API Base URL
// In development, Vite's proxy forwards relative paths ('/api/...'), so API_URL defaults to '' (empty string).
// In production, VITE_API_URL can specify a deployed backend domain (e.g. 'https://api.talentverify.com').
const API_URL = import.meta.env.VITE_API_URL || '';

/**
 * Primary HTTP Request Helper
 * 
 * @param {string} path - The API endpoint path (e.g. '/api/health' or '/api/auth')
 * @param {object} options - Fetch options including HTTP method, headers, and body
 * @returns {Promise<object>} - Parsed JSON response from the server
 */
export async function apiRequest(path, options = {}) {
  let response;

  // STEP 2: Configure Request Headers and Execute Network Fetch
  try {
    const headers = new Headers(options.headers || {});

    // WHY THIS STEP: If sending a JSON payload string, we must tell the server
    // that the Content-Type is 'application/json'. We skip this if sending FormData (file uploads),
    // because the browser automatically generates the correct multipart boundary header.
    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    // Execute standard native browser fetch
    response = await fetch(`${API_URL}${path}`, { ...options, headers });
  } catch {
    // WHY THIS STEP: If backend server is down or unreachable, throw a clear actionable message
    throw new Error('Unable to connect to the FastAPI backend at http://127.0.0.1:8000.');
  }

  // STEP 3: Read and Parse the Response Body
  // We read as plain text first so if the server returns non-JSON or HTML, we don't crash unexpectedly
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text || `The server returned an invalid response (${response.status}).`);
  }

  // STEP 4: Handle Non-OK HTTP Status Codes (4xx, 5xx)
  // WHY THIS STEP: FastAPI returns validation errors in a standard 'detail' field.
  // We extract and format these into human-readable messages so the UI displays helpful feedback.
  if (!response.ok) {
    const validation = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join(', ')
      : data.detail;
    throw new Error(validation || data.message || `Request failed (${response.status}).`);
  }

  // Return the parsed JSON data object
  return data;
}

/**
 * Helper to make a JSON request (POST, PUT, PATCH)
 * Automatically converts the Javascript data object into a JSON string.
 */
export function apiJson(path, method, data) {
  return apiRequest(path, { method, body: JSON.stringify(data) });
}

/**
 * Helper to upload FormData (e.g., CV files, profile photos)
 * Uses HTTP POST without manual Content-Type header so browser manages multipart boundaries.
 */
export function apiForm(path, formData) {
  return apiRequest(path, { method: 'POST', body: formData });
}


let csrfToken: string | null = null;

// In-memory bootstrap: read and immediately clear the CSRF token from all global entrypoints
if (typeof window !== 'undefined') {
  const win = window as any;
  if (win.__OLIMPO_CSRF_TOKEN__) {
    csrfToken = win.__OLIMPO_CSRF_TOKEN__;
    try {
      delete win.__OLIMPO_CSRF_TOKEN__;
    } catch (e) {
      win.__OLIMPO_CSRF_TOKEN__ = undefined;
    }
  }
}

/**
 * Returns the CSRF token from the in-memory bootstrap adapter.
 * It is never persisted in localStorage or sessionStorage.
 */
export function getCsrfToken(): string | null {
  return csrfToken;
}

/**
 * Sets the CSRF token in memory. Useful for unit testing.
 */
export function setCsrfTokenForTesting(token: string | null): void {
  csrfToken = token;
}

/**
 * Validates build-time API base URL.
 * Allows only empty/same-origin or http://127.0.0.1:<valid-port>.
 * Rejects remote hosts, credentials, query, fragment and arbitrary paths.
 */
export function validateApiBase(url: string | undefined): boolean {
  if (!url) {
    return true; // empty / same-origin is allowed
  }

  if (typeof window !== 'undefined' && url === window.location.origin) {
    return true; // same-origin is allowed
  }

  try {
    const parsed = new URL(url);

    if (parsed.protocol !== 'http:') {
      return false;
    }

    if (parsed.hostname !== '127.0.0.1') {
      return false;
    }

    if (parsed.username || parsed.password) {
      return false;
    }

    if (parsed.search && parsed.search !== '') {
      return false;
    }

    if (parsed.hash && parsed.hash !== '') {
      return false;
    }

    if (parsed.pathname !== '/' && parsed.pathname !== '') {
      return false;
    }

    if (!parsed.port) {
      return false; // A valid port is required for http://127.0.0.1:<valid-port>
    }

    const portNum = parseInt(parsed.port, 10);
    if (isNaN(portNum) || portNum < 1 || portNum > 65535 || String(portNum) !== parsed.port) {
      return false;
    }

    return true;
  } catch (e) {
    return false;
  }
}

/**
 * Returns the API base URL.
 * Defaults to same-origin or build-time local configuration only.
 * No arbitrary user input is permitted.
 */
export function getApiBaseUrl(): string {
  // Support Vite build-time env configuration
  const envBase = (import.meta as any).env?.VITE_API_BASE_URL;
  if (envBase && validateApiBase(envBase)) {
    return envBase;
  }
  
  if (typeof window !== 'undefined') {
    return window.location.origin;
  }
  return '';
}


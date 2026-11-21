// The read API (V1.7) and the goal endpoints, with errors turned into
// sentences a presenter can act on.

const KEY = "amos.key";

export function loadKey() {
  try {
    return sessionStorage.getItem(KEY) || localStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
}

export function saveKey(key, remember) {
  try {
    sessionStorage.setItem(KEY, key);
    if (remember) localStorage.setItem(KEY, key);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage can be unavailable; the key then lasts for this page only */
  }
}

export function forgetKey() {
  try {
    sessionStorage.removeItem(KEY);
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to forget */
  }
}

export function keyIsRemembered() {
  try {
    return Boolean(localStorage.getItem(KEY));
  } catch {
    return false;
  }
}

export class ApiError extends Error {
  constructor(status, type, message, details) {
    super(message);
    this.status = status;
    this.type = type;
    this.details = details || {};
  }
}

function midnightPacificIn() {
  const now = new Date();
  const pacific = new Date(now.toLocaleString("en-US", { timeZone: "America/Los_Angeles" }));
  const next = new Date(pacific);
  next.setHours(24, 0, 0, 0);
  const minutes = Math.round((next - pacific) / 60000);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
}

// What to tell the presenter, per failure. Errors say what happened and what to
// do, in the interface's voice.
export function explain(error) {
  if (!(error instanceof ApiError)) {
    return { title: "AMOS is not reachable", detail: "Start the API with `make run`, then try again." };
  }
  if (error.status === 401) {
    return { title: "This key was not accepted", detail: "Paste the API key for your AMOS user in Settings." };
  }
  if (error.type === "ProviderRateLimitError") {
    const scope = error.details.quota_scope;
    if (scope === "day") {
      return {
        title: "Today's Gemini quota is used up",
        detail: `It resets at midnight Pacific, in ${midnightPacificIn()}. Earlier runs stay viewable in History.`,
      };
    }
    return { title: "Gemini's per-minute limit was reached", detail: "Wait a minute, then run again." };
  }
  if (error.type === "ProviderUnavailableError") {
    return {
      title: "Gemini is overloaded",
      detail: "AMOS retried once and Gemini was still busy. Try again in a minute.",
    };
  }
  if (error.type === "ProviderTimeoutError") {
    return { title: "Gemini did not answer in time", detail: "The run stopped at its time limit. Try again." };
  }
  if (error.status === 422) return { title: "AMOS could not accept that", detail: error.message };
  if (error.status === 503) return { title: "AMOS has no database", detail: error.message };
  return { title: `Something failed (${error.type || error.status})`, detail: error.message };
}

export class Api {
  constructor(getKey) {
    this.getKey = getKey;
  }

  headers(extra) {
    const key = this.getKey();
    return {
      "content-type": "application/json",
      ...(key ? { authorization: `Bearer ${key}` } : {}),
      ...(extra || {}),
    };
  }

  async request(method, path, { params, body, headers } = {}) {
    const url = new URL(path, window.location.origin);
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
    let response;
    try {
      response = await fetch(url, {
        method,
        headers: this.headers(headers),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (cause) {
      throw new ApiError(0, "NetworkError", String(cause));
    }
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (!response.ok) {
      const envelope = data && data.error;
      if (envelope) throw new ApiError(response.status, envelope.type, envelope.message, envelope.details);
      const detail = data && data.detail;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg).join("; ")
        : detail || response.statusText;
      throw new ApiError(response.status, `HTTP ${response.status}`, message);
    }
    return data;
  }

  get(path, params) {
    return this.request("GET", path, { params });
  }

  post(path, body, headers) {
    return this.request("POST", path, { body, headers });
  }
}

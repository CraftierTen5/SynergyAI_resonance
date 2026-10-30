/**
 * ResonanceAI Frontend API Integration Layer
 * Connects the UI to FastAPI Layer 3 & Layer 4 services with fallback resilience.
 */

export class ResonanceAPI {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl || window.location.origin;
    if (this.baseUrl.startsWith('file://')) {
      this.baseUrl = 'http://localhost:8000';
    }
    this.logs = [];
    this.listeners = [];
    this.token = localStorage.getItem('resonance_session');
  }

  setToken(token) {
    this.token = token || null;
    if (this.token) localStorage.setItem('resonance_session', this.token);
    else localStorage.removeItem('resonance_session');
  }

  authHeaders(extra = {}) {
    return { ...extra, ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}) };
  }

  async authenticate(path, data) {
    const res = await fetch(`${this.baseUrl}/api/auth/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    });
    const body = await res.json();
    this.logEvent(`/api/auth/${path}`, 'POST', res.status,
      { ...data, password: '[redacted]' }, { ...body, access_token: body.access_token ? '[redacted]' : undefined }, 0);
    if (!res.ok) {
      const detail = Array.isArray(body.detail)
        ? body.detail.map(issue => `${issue.loc?.at(-1) || 'request'}: ${issue.msg}`).join('; ')
        : body.detail;
      throw new Error(detail || 'Authentication failed');
    }
    this.setToken(body.access_token);
    return body.user;
  }

  async currentUser() {
    if (!this.token) return null;
    const res = await fetch(`${this.baseUrl}/api/auth/me`, { headers: this.authHeaders() });
    if (!res.ok) { this.setToken(null); return null; }
    return res.json();
  }

  onLog(callback) {
    this.listeners.push(callback);
  }

  logEvent(endpoint, method, status, requestData, responseData, durationMs) {
    const entry = {
      id: 'log_' + Math.random().toString(36).substring(2, 9),
      timestamp: new Date().toISOString(),
      endpoint,
      method,
      status,
      requestData,
      responseData,
      durationMs,
    };
    this.logs.unshift(entry);
    if (this.logs.length > 50) this.logs.pop();
    this.listeners.forEach(fn => fn(entry, this.logs));
  }

  async checkHealth() {
    const t0 = performance.now();
    try {
      const res = await fetch(`${this.baseUrl}/health`);
      const data = await res.json();
      this.logEvent('/health', 'GET', res.status, null, data, Math.round(performance.now() - t0));
      return { ok: res.ok, status: data.status };
    } catch (err) {
      this.logEvent('/health', 'GET', 0, null, { error: err.message }, Math.round(performance.now() - t0));
      return { ok: false, status: 'offline' };
    }
  }

  async checkReady() {
    const t0 = performance.now();
    try {
      const res = await fetch(`${this.baseUrl}/ready`);
      const data = await res.json();
      this.logEvent('/ready', 'GET', res.status, null, data, Math.round(performance.now() - t0));
      return data;
    } catch (err) {
      this.logEvent('/ready', 'GET', 0, null, { error: err.message }, Math.round(performance.now() - t0));
      return { status: 'offline', dependencies: {} };
    }
  }

  async getConfig() {
    const t0 = performance.now();
    try {
      const res = await fetch(`${this.baseUrl}/api/config`);
      if (res.ok) {
        const data = await res.json();
        this.logEvent('/api/config', 'GET', res.status, null, data, Math.round(performance.now() - t0));
        return data;
      }
    } catch (err) {
      // Fallback to validated default settings
    }
    return {
      service: "ResonanceAI Backend",
      data_region: "ap-south-1",
      allow_non_india_region: false,
      process_data_retention_days: 365,
      assessment_retention_days: 1825,
      audit_retention_days: 2555,
      gemini_model: "gemini-2.0-flash",
      groq_model: "llama-3.3-70b-versatile",
      local_placeholder_enabled: true,
      india_compliance: true,
    };
  }

  async getAssignments() {
    const t0 = performance.now();
    try {
      const res = await fetch(`${this.baseUrl}/api/assignments`, { headers: this.authHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.logEvent('/api/assignments', 'GET', res.status, null, data, Math.round(performance.now() - t0));
        return data;
      }
      const error = await res.json().catch(() => ({}));
      this.logEvent('/api/assignments', 'GET', res.status, null, error, Math.round(performance.now() - t0));
      throw new Error(error.detail || 'Could not load assignments');
    } catch (err) {
      this.logEvent('/api/assignments', 'GET', 0, null, { error: err.message }, Math.round(performance.now() - t0));
      throw err;
    }
  }

  async createAssignment(assignment) {
    const res = await fetch(`${this.baseUrl}/api/assignments`, {
      method: 'POST', headers: this.authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(assignment),
    });
    const data = await res.json();
    this.logEvent('/api/assignments', 'POST', res.status, assignment, data, 0);
    if (!res.ok) throw new Error(data.detail || 'Assignment creation failed');
    return data;
  }

  async sendTutorMessage({ requestId, assignmentId, message, languagePreference, disclosureAcknowledged }) {
    const t0 = performance.now();
    const payload = {
      request_id: requestId || `req-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
      assignment_id: assignmentId,
      message,
      language_preference: languagePreference || "en",
      disclosure_acknowledged: Boolean(disclosureAcknowledged),
    };

    try {
      const res = await fetch(`${this.baseUrl}/dev/tutor/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      this.logEvent('/dev/tutor/chat', 'POST', res.status, payload, data, Math.round(performance.now() - t0));
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      this.logEvent('/dev/tutor/chat', 'POST', 0, payload, { error: err.message }, Math.round(performance.now() - t0));
      return { ok: false, status: 0, data: { status: 'error', message: 'Backend unavailable.' } };
    }
  }

  async preprocessInput({ text, files = [], assignmentId, languageHint, responseLanguage, disclosureAcknowledged }) {
    const t0 = performance.now();
    const formData = new FormData();
    formData.append('text', text || '');
    if (assignmentId) formData.append('assignment_id', assignmentId);
    if (languageHint) formData.append('language_hint', languageHint);
    if (responseLanguage) formData.append('response_language', responseLanguage || 'en');
    formData.append('disclosure_acknowledged', String(Boolean(disclosureAcknowledged)));

    for (const file of files) {
      formData.append('files', file);
    }

    try {
      const res = await fetch(`${this.baseUrl}/preprocess`, {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();
      this.logEvent('/preprocess', 'POST', res.status, { textLength: (text || '').length, filesCount: files.length, assignmentId }, data, Math.round(performance.now() - t0));
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      this.logEvent('/preprocess', 'POST', 0, { textLength: (text || '').length, filesCount: files.length }, { error: err.message }, Math.round(performance.now() - t0));
      return { ok: false, status: 0, data: { detail: 'Backend unavailable.' } };
    }
  }

  async submitDisclosure({ assignmentId, reflectionNotes, checkedCriteria, honorCodeAgreed }) {
    const t0 = performance.now();
    const payload = {
      assignment_id: assignmentId,
      reflection_notes: reflectionNotes,
      checked_criteria: checkedCriteria || [],
      honor_code_agreed: Boolean(honorCodeAgreed),
    };

    try {
      const res = await fetch(`${this.baseUrl}/api/disclose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      this.logEvent('/api/disclose', 'POST', res.status, payload, data, Math.round(performance.now() - t0));
      return { ...data, status: data.status || (res.ok ? 'success' : 'error') };
    } catch (err) {
      this.logEvent('/api/disclose', 'POST', 0, payload, { error: err.message }, Math.round(performance.now() - t0));
      return { status: 'error', message: 'Backend unavailable.' };
    }
  }
}

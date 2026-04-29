// Simple fetch wrapper with error handling
const API_BASE = '';

async function apiRequest(path, options = {}) {
  const opts = {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  };
  if (opts.body && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      try { detail = await res.text(); } catch {}
    }
    const err = new Error(`${res.status}: ${detail}`);
    err.status = res.status;
    throw err;
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

window.api = {
  health: () => apiRequest('/api/health'),
  network: () => apiRequest('/api/network'),

  profiles: {
    list: () => apiRequest('/api/profiles'),
    create: (data) => apiRequest('/api/profiles', { method: 'POST', body: data }),
    get: (id) => apiRequest(`/api/profiles/${id}`),
    delete: (id) => apiRequest(`/api/profiles/${id}`, { method: 'DELETE' }),
  },

  auth: {
    login: (profile_id, pin) => apiRequest('/api/auth/login', {
      method: 'POST',
      body: { profile_id, pin: pin || null },
    }),
    logout: () => apiRequest('/api/auth/logout', { method: 'POST' }),
    me: () => apiRequest('/api/auth/me'),
  },

  settings: {
    get: () => apiRequest('/api/settings'),
    update: (data) => apiRequest('/api/settings', { method: 'PUT', body: data }),
    models: () => apiRequest('/api/settings/llm-models'),
    testLLM: (provider, api_key, model) => apiRequest('/api/settings/test-llm', {
      method: 'POST',
      body: { provider, api_key, model },
    }),
  },

  listings: {
    list: (params = {}) => {
      const q = new URLSearchParams();
      if (params.status) q.append('status', params.status);
      if (params.exclude_status) q.append('exclude_status', params.exclude_status);
      if (params.min_score != null) q.append('min_score', params.min_score);
      if (params.order_by) q.append('order_by', params.order_by);
      if (params.limit) q.append('limit', params.limit);
      const qs = q.toString();
      return apiRequest('/api/listings' + (qs ? '?' + qs : ''));
    },
    stats: () => apiRequest('/api/listings/stats'),
    get: (id) => apiRequest(`/api/listings/${id}`),
    create: (data, { forceAdd = false } = {}) => {
      const qs = forceAdd ? '?force_add=1' : '';
      return apiRequest(`/api/listings${qs}`, { method: 'POST', body: data });
    },
    update: (id, data) => apiRequest(`/api/listings/${id}`, { method: 'PUT', body: data }),
    delete: (id) => apiRequest(`/api/listings/${id}`, { method: 'DELETE' }),
    evaluate: (id) => apiRequest(`/api/listings/${id}/evaluate`, { method: 'POST' }),
    filterSweepPreview: () => apiRequest('/api/listings/filter-sweep/preview'),
    filterSweepApply: () => apiRequest('/api/listings/filter-sweep/apply', { method: 'POST' }),
    pass: (id, reason, note) => apiRequest(`/api/listings/${id}/pass`, {
      method: 'POST', body: { reason, note: note || null }
    }),
    reconsider: (id) => apiRequest(`/api/listings/${id}/reconsider`, { method: 'POST' }),
    listPassed: () => apiRequest('/api/listings/passed/list'),
    togglePassCalibration: (id, useForCalibration) => apiRequest(`/api/listings/${id}/pass/calibration`, {
      method: 'PUT', body: { use_for_calibration: useForCalibration }
    }),
    clearAllPasses: () => apiRequest('/api/listings/passed/all', { method: 'DELETE' }),
    // Tailored resume + cover letter markdown editor endpoints
    getTailoredResume: (id) => apiRequest(`/api/listings/${id}/tailored-resume`),
    saveTailoredResume: (id, markdown) => apiRequest(`/api/listings/${id}/tailored-resume`, {
      method: 'PUT', body: { markdown }
    }),
    revertTailoredResume: (id) => apiRequest(`/api/listings/${id}/tailored-resume/revert`, { method: 'POST' }),
    getCoverLetterMd: (id) => apiRequest(`/api/listings/${id}/cover-letter-md`),
    saveCoverLetterMd: (id, markdown) => apiRequest(`/api/listings/${id}/cover-letter-md`, {
      method: 'PUT', body: { markdown }
    }),
    revertCoverLetterMd: (id) => apiRequest(`/api/listings/${id}/cover-letter-md/revert`, { method: 'POST' }),
    // Chat editor endpoints (shared thread across resume + cover letter)
    getChat: (id) => apiRequest(`/api/listings/${id}/chat`),
    chatTurn: (id, message, scope) => apiRequest(`/api/listings/${id}/chat/turn`, {
      method: 'POST', body: { message, scope }
    }),
    chatApply: (id, turn_index, edit_id) => apiRequest(`/api/listings/${id}/chat/apply`, {
      method: 'POST', body: { turn_index, edit_id }
    }),
    chatReject: (id, turn_index, edit_id) => apiRequest(`/api/listings/${id}/chat/reject`, {
      method: 'POST', body: { turn_index, edit_id }
    }),
    chatUndo: (id) => apiRequest(`/api/listings/${id}/chat/undo`, { method: 'POST' }),
    chatClear: (id) => apiRequest(`/api/listings/${id}/chat`, { method: 'DELETE' }),
    // Convenience wrappers that live on listings rather than resumes (same backend endpoint)
    tailorListing: (id, intensity = null) => {
      const qs = intensity ? `?intensity=${encodeURIComponent(intensity)}` : '';
      return apiRequest(`/api/listings/${id}/tailor${qs}`, { method: 'POST' });
    },
    coverLetterListing: (id, tone = null) => {
      const qs = tone ? `?tone=${encodeURIComponent(tone)}` : '';
      return apiRequest(`/api/listings/${id}/cover-letter${qs}`, { method: 'POST' });
    },
  },

  usage: {
    summary: () => apiRequest('/api/usage/summary'),
  },

  resumes: {
    getCv: () => apiRequest('/api/resumes/cv'),
    saveCv: (markdown) => apiRequest('/api/resumes/cv', { method: 'PUT', body: { markdown } }),
    uploadPdf: async (file) => {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/resumes/upload-pdf', {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { const b = await res.json(); detail = b.detail || JSON.stringify(b); } catch {}
        const e = new Error(`${res.status}: ${detail}`);
        e.status = res.status;
        throw e;
      }
      return res.json();
    },
    listGenerated: () => apiRequest('/api/resumes/generated'),
    listCoverLetters: () => apiRequest('/api/resumes/cover-letters'),
    downloadResumeUrl: (filename) => `/api/resumes/generated/${encodeURIComponent(filename)}`,
    downloadCoverLetterUrl: (filename) => `/api/resumes/cover-letters/${encodeURIComponent(filename)}`,
    tailorListing: (listingId, intensity = null) => {
      const qs = intensity ? `?intensity=${encodeURIComponent(intensity)}` : '';
      return apiRequest(`/api/listings/${listingId}/tailor${qs}`, { method: 'POST' });
    },
    coverLetterListing: (listingId, tone = null) => {
      const qs = tone ? `?tone=${encodeURIComponent(tone)}` : '';
      return apiRequest(`/api/listings/${listingId}/cover-letter${qs}`, { method: 'POST' });
    },

    analyze: (markdown) => apiRequest('/api/resumes/analyze', {
      method: 'POST',
      body: { markdown: markdown || null },
    }),
    applySuggestions: (data) => apiRequest('/api/resumes/apply-suggestions', {
      method: 'POST',
      body: data,
    }),
  },

  scanner: {
    listCompanies: () => apiRequest('/api/scanner/companies'),
    createCompany: (data) => apiRequest('/api/scanner/companies', { method: 'POST', body: data }),
    updateCompany: (id, data) => apiRequest(`/api/scanner/companies/${id}`, { method: 'PUT', body: data }),
    deleteCompany: (id) => apiRequest(`/api/scanner/companies/${id}`, { method: 'DELETE' }),
    loadDefaults: () => apiRequest('/api/scanner/companies/load-defaults', { method: 'POST' }),
    scanNow: (autoEvaluate = false) => apiRequest(`/api/scanner/scan?auto_evaluate=${autoEvaluate}`, { method: 'POST' }),
    scanCompany: (id) => apiRequest(`/api/scanner/companies/${id}/scan`, { method: 'POST' }),
    getTitleFilter: () => apiRequest('/api/scanner/title-filter'),
    updateTitleFilter: (data) => apiRequest('/api/scanner/title-filter', { method: 'PUT', body: data }),

    // AI Company Monitor
    getQueryPlan: (id) => apiRequest(`/api/scanner/companies/${id}/query-plan`),
    regenerateQueryPlan: (id) => apiRequest(`/api/scanner/companies/${id}/query-plan/regenerate`, { method: 'POST' }),
    editQueryPlan: (id, plan) => apiRequest(`/api/scanner/companies/${id}/query-plan`, { method: 'PUT', body: plan }),
    runAiScanForCompany: (id) => apiRequest(`/api/scanner/companies/${id}/ai-scan`, { method: 'POST' }),
    aiBootstrap: (id) => apiRequest(`/api/scanner/companies/${id}/ai-bootstrap`, { method: 'POST' }),
    runAiScanForProfile: () => apiRequest('/api/scanner/ai-scan', { method: 'POST' }),
    listAiRuns: (limit = 50) => apiRequest(`/api/scanner/ai-runs?limit=${limit}`),
    getAiRun: (id) => apiRequest(`/api/scanner/ai-runs/${id}`),
    promoteFiltered: (runId, index) => apiRequest(`/api/scanner/ai-runs/${runId}/promote-filtered`, {
      method: 'POST', body: { index },
    }),
    // Company Suggestions
    getSuggestions: () => apiRequest('/api/scanner/suggestions'),
    refreshSuggestions: () => apiRequest('/api/scanner/suggestions/refresh', { method: 'POST' }),
    addSuggestion: (id) => apiRequest(`/api/scanner/suggestions/${id}/add`, { method: 'POST' }),
    dismissSuggestion: (id) => apiRequest(`/api/scanner/suggestions/${id}/dismiss`, { method: 'POST' }),
  },

  gmail: {
    status: () => apiRequest('/api/gmail/status'),
    pendingCount: () => apiRequest('/api/gmail/pending-count'),
    extractFromEmail: (emailId, force = false) => apiRequest(`/api/gmail/messages/${emailId}/extract${force ? '?force=1' : ''}`, { method: 'POST' }),
    extractPending: (includeProcessedZero = false) => apiRequest(`/api/gmail/extract-pending${includeProcessedZero ? '?include_processed_zero=1' : ''}`, { method: 'POST' }),
    promoteFiltered: (emailId, idx) => apiRequest(`/api/gmail/messages/${emailId}/promote-filtered/${idx}`, { method: 'POST' }),
    uploadCredentials: async (file) => {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/gmail/credentials', {
        method: 'POST', credentials: 'include', body: form,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { const b = await res.json(); detail = b.detail || JSON.stringify(b); } catch {}
        const e = new Error(`${res.status}: ${detail}`); e.status = res.status; throw e;
      }
      return res.json();
    },
    clearCredentials: () => apiRequest('/api/gmail/credentials', { method: 'DELETE' }),
    connect: () => apiRequest('/api/gmail/connect'),
    disconnectAccount: (id) => apiRequest(`/api/gmail/accounts/${id}`, { method: 'DELETE' }),
    syncNow: () => apiRequest('/api/gmail/sync', { method: 'POST' }),
    listMessages: (category = null, limit = 100) => {
      const q = new URLSearchParams();
      if (category) q.append('category', category);
      if (limit) q.append('limit', limit);
      const qs = q.toString();
      return apiRequest('/api/gmail/messages' + (qs ? '?' + qs : ''));
    },
    getMessageDetail: (emailId) => apiRequest(`/api/gmail/messages/${emailId}`),
  },

  history: {
    list: (limit = 100, eventType = null) => {
      const q = new URLSearchParams();
      if (limit) q.append('limit', limit);
      if (eventType) q.append('event_type', eventType);
      const qs = q.toString();
      return apiRequest('/api/history' + (qs ? '?' + qs : ''));
    },
  },

  reminders: {
    list: (includeDismissed = false) => apiRequest(`/api/reminders?include_dismissed=${includeDismissed}`),
    regenerate: () => apiRequest('/api/reminders/regenerate', { method: 'POST' }),
    dismiss: (id) => apiRequest(`/api/reminders/${id}/dismiss`, { method: 'POST' }),
  },

  companies: {
    list: () => apiRequest('/api/companies'),
    research: (name, careers_url, force = false) => apiRequest(`/api/companies/research?force_refresh=${force}`, {
      method: 'POST', body: { name, careers_url: careers_url || null }
    }),
    refresh: (id) => apiRequest(`/api/companies/${id}/refresh`, { method: 'POST' }),
    delete: (id) => apiRequest(`/api/companies/${id}`, { method: 'DELETE' }),
  },

  interview: {
    getStories: () => apiRequest('/api/interview-prep/stories'),
    generate: (force = false) => apiRequest(`/api/interview-prep/stories/generate?force_refresh=${force}`, { method: 'POST' }),
  },

  scheduler: {
    status: () => apiRequest('/api/scheduler/status'),
  },

  backup: {
    exportUrl: () => '/api/backup/export',
    import: async (file) => {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/backup/import', {
        method: 'POST', credentials: 'include', body: form,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { const b = await res.json(); detail = b.detail || JSON.stringify(b); } catch {}
        const e = new Error(`${res.status}: ${detail}`); e.status = res.status; throw e;
      }
      return res.json();
    },
    resetScannerDefaults: () => apiRequest('/api/backup/scanner-reset-defaults', { method: 'POST' }),
  },
};

/**
 * FastAPI client. The only module that knows endpoint paths, so a change
 * in the API surface is a change in exactly one file.
 */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError('Cannot reach the server. Is it still running?', 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.detail || response.statusText, response.status);
  }
  return response.json();
}

const query = (params) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') search.set(k, v);
  });
  const s = search.toString();
  return s ? `?${s}` : '';
};

export const api = {
  stats: () => request('/api/stats'),

  documents: () => request('/api/documents'),
  deleteDocument: (docId) => request(`/api/documents/${docId}`, { method: 'DELETE' }),
  uploadDocument(file) {
    const form = new FormData();
    form.append('file', file);
    return request('/api/documents', { method: 'POST', body: form });
  },

  claims: (params = {}) => request(`/api/claims${query({ limit: 100, ...params })}`),

  relations: (params = {}) => request(`/api/relations${query({ limit: 200, ...params })}`),
  rebuildRelations: () => request('/api/relations/rebuild', { method: 'POST' }),
  counterfactuals: (relationId) => request(`/api/relations/${relationId}/counterfactuals`),

  jobs: () => request('/api/jobs'),
};

export { ApiError };

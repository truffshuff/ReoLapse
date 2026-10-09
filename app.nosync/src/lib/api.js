// Thin client for the reolapse Python server (same origin in the app, proxied in dev).

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  health: () => request('GET', '/api/health'),
  days: () => request('GET', '/api/days'),
  cameraDay: (day, cam, fresh = false) =>
    request('GET', `/api/days/${day}/${encodeURIComponent(cam)}${fresh ? '?fresh=1' : ''}`),
  jobs: () => request('GET', '/api/jobs'),
  job: (id) => request('GET', `/api/jobs/${id}`),
  submit: (kind, day, cameras, options = {}) => request('POST', '/api/jobs', { kind, day, cameras, options }),
  cancel: (id) => request('POST', `/api/jobs/${id}/cancel`),
  retry: (id) => request('POST', `/api/jobs/${id}/retry`),
  queue: () => request('GET', '/api/queue'),
  pauseQueue: () => request('POST', '/api/queue/pause'),
  resumeQueue: () => request('POST', '/api/queue/resume'),
  remove: (id) => request('DELETE', `/api/jobs/${id}`),
  clearFinished: () => request('DELETE', '/api/jobs'),
  move: (id, delta) => request('POST', `/api/jobs/${id}/move?delta=${delta}`),
  settings: () => request('GET', '/api/settings'),
  saveSettings: (patch) => request('PUT', '/api/settings', patch),
  nvr: () => request('GET', '/api/nvr'),
  saveNvr: (login) => request('PUT', '/api/nvr', login),
  models: () => request('GET', '/api/models'),
  correct: (day, cam, id, value) =>
    request('PUT', `/api/days/${day}/${encodeURIComponent(cam)}/objects/${encodeURIComponent(id)}/correction`, { value }),
  nightly: (preview = false) => request('GET', `/api/nightly${preview ? '?preview=1' : ''}`),
  nightlyRun: () => request('POST', '/api/nightly/run'),
  storage: (refresh = false) => request('GET', `/api/storage${refresh ? '?refresh=1' : ''}`),
  cleanup: () => request('POST', '/api/storage/cleanup'),
  sharing: () => request('GET', '/api/storage/sharing'),
  shareNow: () => request('POST', '/api/storage/share'),
  testNvr: () => request('POST', '/api/nvr/test'),
  update: (check = false, force = false) => request('GET', `/api/update?check=${check ? 1 : 0}&force=${force ? 1 : 0}`),
  saveUpdateToken: (token) => request('PUT', '/api/update/token', { token }),
  downloadUpdate: () => request('POST', '/api/update/download'),
  objects: (day, cam, regions, categories = []) =>
    request('GET', `/api/days/${day}/${encodeURIComponent(cam)}/objects?regions=${encodeURIComponent(
      regions.map((r) => [r.x0, r.y0, r.x1, r.y1].map((v) => v.toFixed(4)).join(',')).join(';'))}&categories=${categories.join(',')}`),
};

export const media = {
  out: (day, cam, name, w = 0) => `/api/days/${day}/${encodeURIComponent(cam)}/out/${encodeURIComponent(name)}${w ? `?w=${w}` : ''}`,
  bg: (day, cam, slot, w = 0) => `/api/days/${day}/${encodeURIComponent(cam)}/bg/${slot}${w ? `?w=${w}` : ''}`,
  thumb: (day, cam, id, size = 0) =>
    `/api/days/${day}/${encodeURIComponent(cam)}/objects/${encodeURIComponent(id)}/thumb${size ? `?size=${size}` : ''}`,
  clip: (day, cam, t, dur = 60) => `/api/days/${day}/${encodeURIComponent(cam)}/clip?t=${Math.round(t)}&dur=${dur}`,
};

/** Subscribe to job events. Returns an unsubscribe function. */
export function onJobEvents({ snapshot, job, queue, removed }) {
  const es = new EventSource('/api/events');
  es.addEventListener('snapshot', (e) => snapshot?.(JSON.parse(e.data)));
  es.addEventListener('job', (e) => job?.(JSON.parse(e.data)));
  es.addEventListener('queue', (e) => queue?.(JSON.parse(e.data)));
  es.addEventListener('removed', (e) => removed?.(JSON.parse(e.data)));
  return () => es.close();
}

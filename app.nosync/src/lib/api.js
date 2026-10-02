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
  settings: () => request('GET', '/api/settings'),
  saveSettings: (patch) => request('PUT', '/api/settings', patch),
  nvr: () => request('GET', '/api/nvr'),
  models: () => request('GET', '/api/models'),
  testNvr: () => request('POST', '/api/nvr/test'),
  objects: (day, cam, regions, categories = []) =>
    request('GET', `/api/days/${day}/${encodeURIComponent(cam)}/objects?regions=${encodeURIComponent(
      regions.map((r) => [r.x0, r.y0, r.x1, r.y1].map((v) => v.toFixed(4)).join(',')).join(';'))}&categories=${categories.join(',')}`),
};

export const media = {
  out: (day, cam, name) => `/api/days/${day}/${encodeURIComponent(cam)}/out/${encodeURIComponent(name)}`,
  bg: (day, cam, slot) => `/api/days/${day}/${encodeURIComponent(cam)}/bg/${slot}`,
  thumb: (day, cam, id) => `/api/days/${day}/${encodeURIComponent(cam)}/objects/${encodeURIComponent(id)}/thumb`,
  clip: (day, cam, t, dur = 60) => `/api/days/${day}/${encodeURIComponent(cam)}/clip?t=${Math.round(t)}&dur=${dur}`,
};

/** Subscribe to job events. Returns an unsubscribe function. */
export function onJobEvents({ snapshot, job }) {
  const es = new EventSource('/api/events');
  es.addEventListener('snapshot', (e) => snapshot?.(JSON.parse(e.data)));
  es.addEventListener('job', (e) => job?.(JSON.parse(e.data)));
  return () => es.close();
}

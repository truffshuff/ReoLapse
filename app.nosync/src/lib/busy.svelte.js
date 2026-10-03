// Shared, app-wide: is a job currently downloading? (Footage then loads slowly from the share.)
import { onJobEvents } from './api.js';

export const jobState = $state({ downloading: false, running: false, paused: false, reason: '', auto: false });

const jobs = new Map();
function recompute() {
  const running = [...jobs.values()].filter((j) => j.status === 'running');
  jobState.running = running.length > 0;
  jobState.downloading = running.some((j) => j.progress?.stage === 'download' || j.kind === 'fetch');
}

onJobEvents({
  snapshot: (list) => { jobs.clear(); list.forEach((j) => jobs.set(j.id, j)); recompute(); },
  job: (e) => { jobs.set(e.job.id, e.job); recompute(); },
  queue: (q) => { jobState.paused = q.paused; jobState.reason = q.reason; jobState.auto = q.auto; },
});

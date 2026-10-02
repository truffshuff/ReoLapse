export const CAT_LABEL = { person: 'people', vehicle: 'vehicles', animal: 'animals' };

/** Seconds since midnight -> "4:17 PM" (or "4:17:05 PM" with seconds). */
export function clock(t, seconds = false) {
  const h = Math.floor(t / 3600) % 24;
  const m = Math.floor((t % 3600) / 60);
  const s = Math.floor(t % 60);
  const h12 = h % 12 || 12;
  const mm = String(m).padStart(2, '0');
  return `${h12}:${mm}${seconds ? ':' + String(s).padStart(2, '0') : ''} ${h < 12 ? 'AM' : 'PM'}`;
}

/** "HH:MM" from an <input type=time> -> seconds since midnight. */
export function parseClock(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 3600 + m * 60;
}

export function longDay(day) {
  return new Date(day + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

export function shortDay(day) {
  const d = new Date(day + 'T12:00:00');
  return { dow: d.toLocaleDateString(undefined, { weekday: 'short' }), date: d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) };
}

/** Local-calendar yesterday as YYYY-MM-DD (not UTC, which is off by a day in the evening). */
export function yesterday() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function duration(sec) {
  if (sec == null) return '';
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}

export const STATUS = {
  rendered: { label: 'Ready', cls: 'ok' },
  analyzed: { label: 'Analyzed', cls: 'run' },
  fetched: { label: 'Downloaded', cls: '' },
  empty: { label: 'No data', cls: '' },
};

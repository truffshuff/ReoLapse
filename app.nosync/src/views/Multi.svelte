<script>
  import ZoomControls from '../components/ZoomControls.svelte';
  import { Zoom, zoomable, viewBox } from '../lib/zoom.svelte.js';
  // Synced original footage: every camera's real recording from the same second, played together.
  import { onMount } from 'svelte';
  import Icon from '../components/Icon.svelte';
  import { api, media } from '../lib/api.js';
  import { clock, longDay, parseClock, duration, CAT_LABEL, STREAM_NAME } from '../lib/format.js';
  import VideoState from '../components/VideoState.svelte';
  import { jobState } from '../lib/busy.svelte.js';

  const CLIP_SECONDS = 90; // shorter clips arrive sooner when the share is busy

  let { params } = $props();

  let days = $state([]);
  let loaded = $state(false);
  let day = $state(params.day ?? '');
  let at = $state(params.t ? '' : '16:15');
  let t = $state(params.t ? Number(params.t) : null);
  let playing = $state(false);
  let videos = $state({});
  let vstate = $state({}); // camera -> loading | ready | error

  // Events near the selected time, from every camera
  const WINDOWS = [5, 15, 60]; // ± minutes
  const LEAD = 5; // start footage this many seconds before the event appears
  let spanMin = $state(15);
  let kinds = $state({ person: true, vehicle: true, animal: true });
  let objects = $state({}); // camera -> all of that day's objects (fetched once per day)
  let objectsFor = $state('');
  let loadingEvents = $state(false);
  let picked = $state(null); // { camera, id }
  let autoplay = $state(false);

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  const zooms = new Map(); // camera -> its own zoom
  const zoomFor = (camera) => zooms.get(camera) ?? zooms.set(camera, new Zoom()).get(camera);
  const lanes = $derived(cameras.map((c) => ({ camera: c.camera, hourly: c.hourly, max: Math.max(1, ...c.hourly) })));
  const events = $derived.by(() => {
    if (t == null) return [];
    const lo = t - spanMin * 60, hi = t + spanMin * 60;
    return Object.entries(objects)
      .flatMap(([camera, list]) => list.filter((o) => kinds[o.category] && o.enter >= lo && o.enter <= hi).map((o) => ({ ...o, camera })))
      .sort((a, b) => a.enter - b.enter);
  });

  // Fetch each analyzed camera's object list once per day (the server keeps an index; this is cheap).
  $effect(() => {
    const d = day, cams = cameras.filter((c) => c.status === 'analyzed' || c.status === 'rendered').map((c) => c.camera);
    if (!d || !loaded || objectsFor === d) return; // wait for the day list, or we'd remember "no cameras"
    objectsFor = d;
    objects = {};
    loadingEvents = true;
    Promise.all(cams.map((c) => api.objects(d, c, []).then((r) => [c, r.objects]).catch(() => [c, []])))
      .then((pairs) => { if (day === d) objects = Object.fromEntries(pairs); })
      .finally(() => (loadingEvents = false));
  });

  // After jumping to an event, start every camera together once they've all loaded (or failed).
  $effect(() => {
    if (!autoplay) return;
    const states = cameras.map((c) => vstate[c.camera]);
    if (states.length && states.every((st) => st && st !== 'loading')) {
      autoplay = false;
      playAll();
    }
  });

  function pick(ev) {
    picked = { camera: ev.camera, id: ev.id };
    load(Math.max(0, ev.enter - LEAD));
    autoplay = true;
  }

  onMount(async () => {
    days = await api.days();
    loaded = true;
    // newest day that has footage (a day still downloading has none yet)
    if (!day || !days.some((d) => d.day === day)) day = (days.find((d) => d.cameras.some((c) => c.recordings > 0)) ?? days[0])?.day ?? '';
    if (t == null) load();
  });

  function load(seconds = parseClock(at)) {
    t = seconds;
    vstate = Object.fromEntries(cameras.map((c) => [c.camera, 'loading']));
    at = `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')}`;
    playing = false;
  }

  function shift(delta) {
    const base = t + (Object.values(videos).find(Boolean)?.currentTime ?? 0);
    load(Math.max(0, Math.min(86399, base + delta)));
  }

  // Each clip starts at the keyframe before t, so align them on their own start offsets before playing.
  async function playAll() {
    const vs = Object.values(videos).filter((v) => v && v.readyState >= 2);
    if (playing) { vs.forEach((v) => v.pause()); playing = false; return; }
    const lead = Math.max(...vs.map((v) => v.currentTime));
    vs.forEach((v) => (v.currentTime = lead));
    await Promise.all(vs.map((v) => v.play().catch(() => {})));
    playing = true;
  }
</script>

<div class="page">
  <header>
    <div>
      <h1>All cameras{day ? ' · ' + longDay(day) : ''}</h1>
      <div class="muted sub">Real recordings from every camera, starting at the same moment</div>
    </div>
    <div class="push row">
      <select bind:value={day} aria-label="Day">{#each days as d}<option value={d.day}>{d.day}</option>{/each}</select>
      <input type="time" bind:value={at} aria-label="Time of day" />
      <button class="btn" onclick={() => load()}>Go</button>
    </div>
  </header>

  {#if !loaded}<div class="empty">Loading…</div>{/if}
  <section class="grid">
    {#each cameras as c (c.camera)}
      <figure class:picked={picked?.camera === c.camera} {@attach zoomable(zoomFor(c.camera))}>
        {#if t != null}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={videos[c.camera]} src={media.clip(day, c.camera, t, CLIP_SECONDS)} muted preload="auto"
                 style:object-view-box={viewBox(zoomFor(c.camera))} style:cursor={zoomFor(c.camera).z > 1 ? 'grab' : null}
                 onloadeddata={() => (vstate[c.camera] = 'ready')} onerror={() => (vstate[c.camera] = 'error')}></video>
          <VideoState status={vstate[c.camera] ?? 'loading'} busy={jobState.downloading} />
          <ZoomControls zoom={zoomFor(c.camera)} />
        {/if}
        <figcaption><span class="tag">{c.camera}{c.footage?.stream ? ` · ${STREAM_NAME[c.footage.stream]}` : ''}</span></figcaption>
      </figure>
    {/each}
  </section>

  <div class="row">
    <button class="btn icon-btn round" aria-label={playing ? 'Pause all' : 'Play all'} onclick={playAll}><Icon name={playing ? 'pause' : 'play'} /></button>
    <span class="mono clock">{t != null ? clock(t, true) : ''}</span>
    <button class="btn" onclick={() => shift(-30)}>− 30 s</button>
    <button class="btn" onclick={() => shift(30)}>+ 30 s</button>
  </div>

  <section class="lanes" aria-label="Activity by camera">
    {#each lanes as l (l.camera)}
      <div class="lane">
        <span class="small muted">{l.camera}</span>
        <div class="bars">
          {#each l.hourly as v, i}
            <button style:--h="{Math.round((24 * v) / l.max)}px" aria-label="{l.camera} at {clock(i * 3600)}" onclick={() => load(i * 3600 + 1800)}></button>
          {/each}
          {#if t != null}<div class="head" style:left="{(t / 86400) * 100}%"></div>{/if}
        </div>
      </div>
    {/each}
  </section>

  <section class="panel events" aria-label="Events near this time">
    <div class="row">
      <h2>Detected near {t != null ? clock(t) : '…'}</h2>
      <div class="seg" role="group" aria-label="Time window">
        {#each WINDOWS as w}<button aria-pressed={spanMin === w} onclick={() => (spanMin = w)}>±{w} min</button>{/each}
      </div>
      <div class="seg" role="group" aria-label="Show">
        {#each Object.keys(kinds) as k}
          <button aria-pressed={kinds[k]} onclick={() => (kinds[k] = !kinds[k])}><span class="dot {k}"></span> {CAT_LABEL[k]}</button>
        {/each}
      </div>
      <span class="small muted push">
        {#if loadingEvents}Loading events…{:else}{events.length} on {Object.keys(objects).length} camera{Object.keys(objects).length === 1 ? '' : 's'}{/if}
      </span>
    </div>
    {#if events.length}
      <ul class="results">
        {#each events as ev (ev.camera + ev.id)}
          <li>
            <button class="result" class:sel={picked?.id === ev.id && picked?.camera === ev.camera} onclick={() => pick(ev)}
                    title="Show every camera from {clock(Math.max(0, ev.enter - LEAD), true)}">
              <img src={media.thumb(day, ev.camera, ev.id)} alt="{ev.category} on {ev.camera} at {clock(ev.enter)}" loading="lazy" />
              <span class="meta">
                <span class="when mono"><span class="dot {ev.category}"></span>{clock(ev.enter, true)}</span>
                <span class="small muted">{ev.camera} · {duration(ev.t1 - ev.t0)}</span>
              </span>
            </button>
          </li>
        {/each}
      </ul>
    {:else if !loadingEvents}
      <div class="small muted">{Object.keys(objects).length ? 'Nothing detected in this window. Try a wider window or another time.' : 'No camera has been analyzed for this day yet.'}</div>
    {/if}
  </section>
</div>

<style>
  .page { padding: 24px 32px 28px; display: flex; flex-direction: column; gap: 16px; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 22px; font-weight: 600; }
  .sub { font-size: 13px; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .push { margin-left: auto; }
  .small { font-size: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; align-items: start; }
  figure { margin: 0; position: relative; border-radius: 12px; overflow: hidden; background: var(--inset); border: 1px solid var(--line); min-height: 140px; }
  video { width: 100%; display: block; }
  figcaption { position: absolute; left: 10px; top: 10px; }
  .tag { padding: 4px 10px; border-radius: 8px; background: rgba(14,16,20,0.85); font-size: 13px; font-weight: 500; }
  .round { border-radius: 22px; background: var(--text); color: var(--bg); border: 0; }
  .clock { font-size: 20px; font-weight: 500; }
  .lanes { display: flex; flex-direction: column; gap: 6px; }
  .lane { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 10px; align-items: center; }
  .bars { position: relative; display: flex; align-items: flex-end; gap: 2px; height: 30px; padding: 0 2px; border-radius: 6px; background: var(--rail); border: 1px solid var(--line); }
  .bars button { flex: 1; height: 100%; padding: 0; border: 0; background: transparent; position: relative; }
  .bars button::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: var(--h); background: var(--accent); }
  .bars button:hover { background: rgba(142, 168, 255, 0.08); }
  figure.picked { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .events { display: flex; flex-direction: column; gap: 12px; }
  h2 { font-size: 15px; font-weight: 600; }
  .results { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
             gap: 10px; max-height: 320px; overflow: auto; }
  .result { width: 100%; padding: 8px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface-2);
            display: flex; flex-direction: column; gap: 6px; text-align: left; }
  .result:hover, .result.sel { border-color: var(--accent); }
  .result img { width: 100%; height: 90px; object-fit: contain; background: var(--inset); border-radius: 6px; }
  .meta { display: flex; flex-direction: column; gap: 2px; }
  .when { display: flex; align-items: center; gap: 6px; font-size: 13px; }
  .head { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--text); }
</style>

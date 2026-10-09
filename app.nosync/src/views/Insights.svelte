<script>
  import { onMount } from 'svelte';
  import { api, media, onJobEvents } from '../lib/api.js';
  import { longDay } from '../lib/format.js';
  import SourceInfo from '../components/SourceInfo.svelte';

  let { params } = $props();

  const SHOW = [['all', 'Everything'], ['person', 'People'], ['vehicle', 'Vehicles'], ['animal', 'Animals']];
  const VIEWS = [
    { id: 'trails', file: 'trails.jpg', label: 'Trails', caption: 'Every path of the day, colored by when it happened.' },
    { id: 'heatmap', file: 'heatmap.jpg', label: 'Heatmap', caption: 'How many objects passed through each spot; each counts once however long it stayed.' },
    { id: 'group', file: 'strobe.jpg', label: 'Group shot', caption: 'One snapshot of everyone who came by in daylight, far ones behind near ones.' },
  ];

  let days = $state([]);
  let loaded = $state(false);
  let day = $state(params.day ?? '');
  let camera = $state(params.camera ?? '');
  let view = $state('trails');
  let show = $state('all');
  let request = $state(null); // { job, day, cameras, status, frac, error } for pictures queued from here

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  const cam = $derived(cameras.find((c) => c.camera === camera));
  const current = $derived(VIEWS.find((v) => v.id === view));
  const available = $derived(new Set((cam?.outputs ?? []).map((o) => o.name)));
  // trails.jpg, or trails_person.jpg for one type
  const file = $derived(show === 'all' ? current.file : current.file.replace('.jpg', `_${show}.jpg`));
  const showLabel = $derived(SHOW.find(([k]) => k === show)?.[1].toLowerCase());
  const hasWhole = $derived(available.has(current.file));
  const mine = $derived(request && request.day === day && request.cameras.includes(camera) ? request : null);

  // A pictures-only render for every rendered camera of the day: whole-day and per-type stills, no videos.
  async function makePictures() {
    const d = day;
    const cams = cameras.filter((c) => c.outputs.length).map((c) => c.camera);
    try {
      const job = await api.submit('render', d, cams, { no_video: true });
      request = { job: job.id, day: d, cameras: cams, status: job.status, frac: 0, error: '' };
    } catch (e) {
      request = { job: null, day: d, cameras: cams, status: 'failed', frac: 0, error: e.message };
    }
  }

  async function onJob(job) {
    if (!request || job.id !== request.job) return;
    request = { ...request, status: job.status, frac: job.progress?.frac ?? request.frac, waiting: job.waiting };
    if (job.status === 'done') {
      await Promise.all(request.cameras.map((c) => api.cameraDay(request.day, c, true).catch(() => null)));
      days = await api.days();
    }
  }

  onMount(() => onJobEvents({ job: (e) => onJob(e.job) }));

  onMount(async () => {
    days = await api.days();
    loaded = true;
    if (!day || !days.some((d) => d.day === day)) day = days.find((d) => d.cameras.some((c) => c.outputs.length))?.day ?? days[0]?.day ?? '';
    if (params.show) show = params.show;
    if (!camera) camera = cameras.find((c) => c.outputs.length)?.camera ?? cameras[0]?.camera ?? '';
  });
</script>

<div class="page">
  <header>
    <div>
      <h1>Insights{camera ? ' · ' + camera : ''}{day ? ' · ' + longDay(day) : ''}</h1>
      <div class="muted sub">Where things moved, all day in one picture</div>
    </div>
    <div class="push row">
      {#if day}<a class="btn" href={`#/report?day=${day}`}>PDF report</a>{/if}
      <select bind:value={day} aria-label="Day">{#each days as d}<option value={d.day}>{d.day}</option>{/each}</select>
      <div class="seg" role="group" aria-label="Camera">
        {#each cameras as c}<button aria-pressed={c.camera === camera} onclick={() => (camera = c.camera)}>{c.camera}</button>{/each}
      </div>
    </div>
  </header>

  <div class="row">
    <div class="seg" role="tablist" aria-label="View">
      {#each VIEWS as v}<button role="tab" aria-selected={v.id === view} onclick={() => (view = v.id)}>{v.label}</button>{/each}
    </div>
    <div class="seg" role="group" aria-label="Show">
      {#each SHOW.filter(([k]) => k === 'all' || (cam?.counts?.[k] ?? 0) > 0) as [k, label]}
        <button aria-pressed={show === k} onclick={() => (show = k)}
                class:missing={k !== 'all' && !available.has(current.file.replace('.jpg', `_${k}.jpg`))}>{label}</button>
      {/each}
    </div>
  </div>

  <section class="stage">
    {#if cam && available.has(file)}
      <img src={media.out(day, camera, file)} alt="{current.label} of {show === 'all' ? 'everything' : showLabel} for {camera}" />
    {:else if !loaded}
      <div class="empty">Loading…</div>
    {:else if show !== 'all' && hasWhole}
      <div class="empty notice">
        {#if mine && (mine.status === 'queued' || mine.status === 'running')}
          <span>Making the pictures (job {mine.job}){mine.status === 'running' ? `: ${Math.round(mine.frac * 100)}%`
            : mine.waiting ? `: ${mine.waiting}` : ': queued'}. They appear here when they're done.</span>
        {:else}
          <span>This day was rendered before Insights had separate pictures for {showLabel}.</span>
          <button class="btn primary" onclick={makePictures}>Make pictures for each type</button>
          <span class="small muted">For every camera on this day: about a minute per camera, and no videos are re-rendered.
            Days rendered from now on get them automatically.</span>
          {#if mine?.status === 'failed' || mine?.status === 'cancelled'}<span class="err small">Job {mine.job ?? ''} {mine.status}{mine.error ? `: ${mine.error}` : ''}; see Jobs.</span>{/if}
        {/if}
      </div>
    {:else}
      <div class="empty">Not rendered yet for this camera and day.</div>
    {/if}
  </section>
  {#if view === 'trails'}
    <div class="legend">
      <div class="grad"></div>
      <div class="axis mono dim"><span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>12a</span></div>
    </div>
  {/if}
  <p class="muted">{current.caption}</p>
  {#if cam}<SourceInfo footage={cam.footage} analysis={cam.analysis} analyzed={cam.analyzed} />{/if}
</div>

<style>
  .page { padding: 24px 32px 32px; display: flex; flex-direction: column; gap: 16px; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 22px; font-weight: 600; }
  .sub { font-size: 13px; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .push { margin-left: auto; }
  .stage { border-radius: 12px; overflow: hidden; background: var(--inset); border: 1px solid var(--line); }
  .stage img { width: 100%; display: block; }
  .notice { display: flex; flex-direction: column; align-items: center; gap: 12px; text-align: center; }
  .seg button.missing { opacity: 0.45; }
  .err { color: var(--bad); }
  .legend { max-width: 640px; }
  .grad { height: 12px; border-radius: 6px;
          background: linear-gradient(90deg, #6E6EFF 0%, #DC5ADC 25%, #FF9628 37.5%, #FFE632 50%, #96E63C 62.5%, #28D2E6 75%, #3C8CFF 87.5%, #6E6EFF 100%); }
  .axis { display: flex; justify-content: space-between; font-size: 11px; margin-top: 6px; }
  p { margin: 0; font-size: 14px; }
</style>

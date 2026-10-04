<script>
  // Interim player: plays the rendered synopsis MP4s and jumps to original footage.
  // Clicking the activity graph moves the synopsis to that time of day and loads the original
  // recording from the same moment. The live, in-app compositor replaces the MP4s in phase 3.
  import { onMount } from 'svelte';
  import { api, media, onJobEvents } from '../lib/api.js';
  import { clock, longDay, parseClock, duration, CAT_LABEL, STREAM_NAME } from '../lib/format.js';
  import RegionOverlay from '../components/RegionOverlay.svelte';
  import VideoState from '../components/VideoState.svelte';
  import SourceInfo from '../components/SourceInfo.svelte';
  import { jobState } from '../lib/busy.svelte.js';

  let { params } = $props();

  let days = $state([]);
  let loaded = $state(false);
  let day = $state(params.day ?? '');
  let camera = $state(params.camera ?? '');
  let info = $state(null);
  let videoName = $state('');
  let synVideo = $state();
  let meta = $state(null); // the video's sidecar: timing and the settings it was made with
  let length = $state(0); // synopsis seconds that span the 24 h day (from the render's sidecar)
  let synTime = $state(0);
  let originalAt = $state('12:00');
  let originalT = $state(null);
  let originalSrc = $state('');
  let synState = $state('loading');
  let origState = $state('loading');

  // Find in area
  let drawing = $state(false);
  let regions = $state([]);
  let kinds = $state({ person: true, vehicle: true, animal: true });
  let found = $state(null);
  let searching = $state(false);
  let searchError = $state('');

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  // synopsis_<style>_<color>[_<types>].mp4 -> { name, style, color, only }
  const videos = $derived((info?.outputs ?? []).filter((o) => o.name.endsWith('.mp4')).map((o) => {
    const [, style, color, only = 'all'] = o.name.replace(/\.mp4$/, '').split('_');
    return { name: o.name, style, color, only };
  }));
  const STYLE_NAMES = ['cutout', 'ghost', 'outline'];
  const SHOW = [['all', 'Everything'], ['person', 'People'], ['vehicle', 'Vehicles'], ['animal', 'Animals']];
  const current = $derived(videos.find((v) => v.name === videoName));
  // Which objects the Show buttons and the graph are on; a type without its own video yet keeps
  // the full video playing and offers to render one.
  let show = $state('all');
  let request = $state(null); // { job, only, status, frac } for a type video queued from here
  const showLabel = $derived(SHOW.find(([k]) => k === show)?.[1].toLowerCase() ?? '');
  const showMissing = $derived(show !== 'all' && !videos.some((v) => v.only === show));
  const has = (style, only) => videos.some((v) => v.style === style && v.only === only);
  // switch one control and keep the other where possible
  function choose(style, only) {
    const pick = videos.find((v) => v.style === style && v.only === only)
      ?? videos.find((v) => v.only === only) ?? videos.find((v) => v.style === style && v.only === 'all');
    if (pick) videoName = pick.name;
  }

  function pickShow(k) {
    show = k;
    if (videos.some((v) => v.only === k)) choose(current?.style ?? 'cutout', k);
    else if (current?.only !== 'all') choose(current?.style ?? 'cutout', 'all');
  }

  async function renderType(only) {
    const d = day, c = camera;
    try {
      const job = await api.submit('render', d, [c], { only: [only] });
      request = { job: job.id, day: d, camera: c, only, status: job.status, frac: 0, error: '' };
    } catch (e) {
      request = { job: null, day: d, camera: c, only, status: 'failed', frac: 0, error: e.message };
    }
  }

  async function onJob(job) {
    if (!request || job.id !== request.job) return;
    request = { ...request, status: job.status, frac: job.progress?.frac ?? request.frac, waiting: job.waiting };
    if (job.status === 'done' && request.day === day && request.camera === camera) {
      info = await api.cameraDay(day, camera, true);
      if (show === request.only) choose(current?.style ?? 'cutout', show);
    }
  }
  const dayT = $derived(length ? Math.min(86399, (synTime / length) * 86400) : null);

  onMount(() => onJobEvents({ job: (e) => onJob(e.job) }));

  onMount(async () => {
    days = await api.days();
    loaded = true;
    if (!day || !days.some((d) => d.day === day)) {
      const withVideo = days.find((d) => d.cameras.some((c) => c.status === 'rendered')) ?? days[0];
      day = withVideo?.day ?? '';
    }
    if (!camera) camera = cameras.find((c) => c.status === 'rendered')?.camera ?? cameras[0]?.camera ?? '';
  });

  $effect(() => {
    if (!day || !camera) return;
    const d = day, c = camera;
    history.replaceState(null, '', `#/player?day=${d}&camera=${encodeURIComponent(c)}`);
    api.cameraDay(d, c).then((x) => {
      info = x;
      const vids = x.outputs.filter((o) => o.name.endsWith('.mp4') && !/_(person|vehicle|animal)/.test(o.name));
      videoName = (vids.find((v) => v.name.includes('cutout')) ?? vids[0] ?? x.outputs.find((o) => o.name.endsWith('.mp4')))?.name ?? '';
      originalSrc = '';
      originalT = null;
      show = 'all';
      regions = [];
      found = null;
    });
  });

  // Re-run the area search whenever the areas or type filters change (debounced).
  $effect(() => {
    const rs = $state.snapshot(regions);
    const cats = Object.entries(kinds).filter(([, on]) => on).map(([k]) => k);
    if (!rs.length || !day || !camera) { found = null; return; }
    const d = day, c = camera;
    const timer = setTimeout(async () => {
      searching = true;
      searchError = '';
      try {
        const res = await api.objects(d, c, rs, cats);
        if (d === day && c === camera) found = res.objects;
      } catch (e) {
        searchError = e.message;
      }
      searching = false;
    }, 300);
    return () => clearTimeout(timer);
  });

  // The render's sidecar says how many video seconds cover the day; older renders fall back to the duration.
  $effect(() => {
    if (!videoName) return;
    length = 0;
    meta = null;
    synState = 'loading';
    fetch(media.out(day, camera, videoName.replace(/\.mp4$/, '.json')))
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => { meta = m; if (m?.length) length = m.length; })
      .catch(() => {});
  });

  function onMeta() {
    if (!length && synVideo?.duration) length = synVideo.duration;
  }

  // The graph's scale: a round top value with an integer midpoint (2, 4, 6, 8, 10, 20, 40…).
  function niceMax(m) {
    if (m <= 2) return 2;
    const p = 10 ** Math.floor(Math.log10(m));
    return [1, 2, 4, 6, 8, 10].map((f) => f * p).find((v) => v >= m);
  }
  const hourly = $derived(show === 'all' ? info?.hourly : info?.hourly_by?.[show] ?? info?.hourly);
  const yMax = $derived(niceMax(Math.max(0, ...(hourly ?? [0]))));
  const PLOT = 54; // px of bar height that yMax fills (the .timeline CSS leaves exactly this)

  function toInput(t) {
    return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}`;
  }

  function showOriginal(t) {
    originalT = t;
    originalAt = toInput(t);
    originalSrc = media.clip(day, camera, t, 120);
    origState = 'loading';
  }

  /** Move the synopsis to time of day `t` and load the original footage from the same moment. */
  function jumpTo(t) {
    t = Math.max(0, Math.min(86399, t));
    if (synVideo && length) {
      synVideo.currentTime = (t / 86400) * length;
      synVideo.play().catch(() => {});
    }
    showOriginal(t);
  }

  // Mouse clicks map the exact x position to a time; keyboard activation (detail 0) uses the hour's middle.
  function onBarClick(e, hour) {
    if (e.detail === 0) return jumpTo(hour * 3600 + 1800);
    const rect = e.currentTarget.parentElement.getBoundingClientRect();
    jumpTo(((e.clientX - rect.left) / rect.width) * 86400);
  }
</script>

<div class="page">
  <header>
    <div>
      <h1>{camera || 'Player'}{day ? ' · ' + longDay(day) : ''}</h1>
      {#if info}<div class="muted sub">{Object.entries(info.counts).map(([k, n]) => `${n} ${CAT_LABEL[k] ?? k}`).join(' · ')}</div>{/if}
    </div>
    <div class="push row">
      <select bind:value={day} aria-label="Day">{#each days as d}<option value={d.day}>{d.day}</option>{/each}</select>
      <div class="seg" role="group" aria-label="Camera">
        {#each cameras as c}
          <button aria-pressed={c.camera === camera} onclick={() => (camera = c.camera)}>{c.camera}</button>
        {/each}
      </div>
    </div>
  </header>

  <section class="stage">
    {#if videoName}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video bind:this={synVideo} src={media.out(day, camera, videoName)} controls autoplay muted
             onloadedmetadata={onMeta} ontimeupdate={() => (synTime = synVideo.currentTime)} onseeked={() => (synTime = synVideo.currentTime)}
             onloadeddata={() => (synState = 'ready')} onerror={() => (synState = 'error')}></video>
      <VideoState status={synState} busy={jobState.downloading} />
      <RegionOverlay bind:regions {drawing} />
    {:else if !loaded || (camera && !info)}
      <div class="empty">Loading…</div>
    {:else}
      <div class="empty">No synopsis rendered for this camera and day yet. Run it from <a href="#/jobs">Jobs</a>.</div>
    {/if}
  </section>

  <div class="toolbar">
    {#if videos.length > 1 && current}
      <div class="seg" role="group" aria-label="Style">
        {#each STYLE_NAMES.filter((st) => videos.some((v) => v.style === st)) as st}
          <button aria-pressed={current.style === st} onclick={() => choose(st, current.only)}
                  class:missing={!has(st, current.only)}
                  title={has(st, current.only) ? '' : `No ${st} video of this yet: the closest one is shown`}>{st}</button>
        {/each}
      </div>
      <div class="seg" role="group" aria-label="Show">
        {#each SHOW.filter(([k]) => k === 'all' || (info?.counts?.[k] ?? 0) > 0) as [k, label]}
          <button aria-pressed={show === k} onclick={() => pickShow(k)} class:missing={!videos.some((v) => v.only === k)}
                  title={videos.some((v) => v.only === k) ? '' : `No ${label.toLowerCase()}-only video for this day yet: click to make one`}>{label}</button>
        {/each}
      </div>
    {/if}
    {#if videoName}
      <div class="push row">
        <button class="btn" class:primary={drawing} aria-pressed={drawing} onclick={() => (drawing = !drawing)}>
          {drawing ? 'Done drawing' : 'Find in area'}
        </button>
        {#if regions.length}<button class="btn quiet" onclick={() => { regions = []; drawing = false; }}>Clear areas</button>{/if}
      </div>
    {/if}
  </div>

  {#if info && (videoName || info.analyzed)}
    <SourceInfo footage={info.footage} analysis={meta?.analysis ?? info.analysis} analyzed={info.analyzed} render={meta} />
  {/if}

  {#if showMissing && info}
    {@const mine = request && request.only === show && request.day === day && request.camera === camera ? request : null}
    <div class="notice panel">
      {#if mine && (mine.status === 'queued' || mine.status === 'running')}
        <span>Making the {showLabel}-only video (job {mine.job}){mine.status === 'running'
          ? `: ${Math.round(mine.frac * 100)}%` : mine.waiting ? `: ${mine.waiting}` : ': queued'}. It appears here when it's done;
          the graph below already shows just the {showLabel}.</span>
      {:else}
        <span>There's no {showLabel}-only video for {camera} on this day yet, so the full video keeps playing; the graph
          below shows just the {showLabel}. Making one takes a few minutes.</span>
        <button class="btn primary" onclick={() => renderType(show)}>Make {showLabel} video</button>
        {#if mine?.status === 'failed' || mine?.status === 'cancelled'}<span class="err small">Job {mine.job ?? ''} {mine.status}{mine.error ? `: ${mine.error}` : ''}; see Jobs.</span>{/if}
        <span class="small muted">To make them for every day, switch them on in <a href="#/settings">Settings → Rendering defaults</a>.</span>
      {/if}
    </div>
  {/if}

  {#if regions.length}
    <section class="panel found" aria-label="Objects in the selected areas">
      <div class="row">
        <h2>In {regions.length === 1 ? 'this area' : `these ${regions.length} areas`}</h2>
        <div class="seg" role="group" aria-label="Show">
          {#each Object.keys(kinds) as k}
            <button aria-pressed={kinds[k]} onclick={() => (kinds[k] = !kinds[k])}><span class="dot {k}"></span> {CAT_LABEL[k]}</button>
          {/each}
        </div>
        <span class="small muted push">
          {#if searching}Searching… (the first search of an older day builds an index and can take a few minutes)
          {:else if searchError}<span class="err">{searchError}</span>
          {:else if found}{found.length} found{/if}
        </span>
      </div>
      {#if found?.length}
        <ul class="results">
          {#each found as o (o.id)}
            <li>
              <button class="result" onclick={() => jumpTo(o.enter)} title="Jump to {clock(o.enter, true)}">
                <img src={media.thumb(day, camera, o.id)} alt="{o.category} at {clock(o.enter)}" loading="lazy" />
                <span class="meta">
                  <span class="when mono"><span class="dot {o.category}"></span>{clock(o.enter)}</span>
                  <span class="small muted">{duration(o.t1 - o.t0)} on camera</span>
                </span>
              </button>
            </li>
          {/each}
        </ul>
      {:else if found && !searching}
        <div class="small muted">Nothing passed through {regions.length === 1 ? 'that area' : 'those areas'}.</div>
      {/if}
    </section>
  {/if}

  {#if info}
    <section aria-label="Activity by hour" class="tl-wrap">
      <div class="tl-head">
        <span class="small"><span class="dot {show}"></span> {show === 'all' ? 'Objects' : SHOW.find(([k]) => k === show)[1]} per hour</span>
        <span class="small muted">· click the graph to jump the synopsis there and show the original footage</span>
        {#if dayT != null}<span class="mono small push">synopsis at {clock(dayT)}</span>{/if}
      </div>
      <div class="chart">
        <div class="yaxis mono dim" aria-hidden="true">
          <span style:bottom="{PLOT + 1}px">{yMax}</span><span style:bottom="{PLOT / 2 + 1}px">{yMax / 2}</span><span style:bottom="1px">0</span>
        </div>
        <div class="timeline {show}">
          <div class="grid-line" style:bottom="{PLOT / 2}px" aria-hidden="true"></div>
          <div class="grid-line" style:bottom="{PLOT}px" aria-hidden="true"></div>
          {#each hourly as v, i}
            {@const what = show === 'all' ? (v === 1 ? 'object' : 'objects') : v === 1 ? show : CAT_LABEL[show]}
            <button style:--h="{v ? Math.max(2, (PLOT * v) / yMax) : 0}px" aria-label="{clock(i * 3600)}: {v} {what}; jump there"
                    title="{clock(i * 3600)}–{clock((i + 1) * 3600)}: {v} {what}" onclick={(e) => onBarClick(e, i)}></button>
          {/each}
          {#if dayT != null}<div class="playhead" style:left="{(dayT / 86400) * 100}%" aria-hidden="true"></div>{/if}
          {#if originalT != null}<div class="orig" style:left="{(originalT / 86400) * 100}%" aria-hidden="true"></div>{/if}
        </div>
        <span></span>
        <div class="axis mono dim"><span>12a</span><span>3a</span><span>6a</span><span>9a</span><span>12p</span><span>3p</span><span>6p</span><span>9p</span><span>12a</span></div>
      </div>
    </section>

    <section class="panel original" aria-label="Original footage">
      <div class="row">
        <h2>Original footage</h2>
        <input type="time" bind:value={originalAt} aria-label="Time of day" />
        <button class="btn" onclick={() => jumpTo(parseClock(originalAt))}>Go</button>
        {#if originalT != null}<span class="small muted">from {clock(originalT, true)}</span>{/if}
        {#if info.footage?.stream}<span class="small muted push">{STREAM_NAME[info.footage.stream]} recording</span>{/if}
      </div>
      {#if originalSrc}
        <!-- svelte-ignore a11y_media_has_caption -->
        <div class="orig-wrap">
          <video src={originalSrc} controls autoplay onloadeddata={() => (origState = 'ready')} onerror={() => (origState = 'error')}></video>
          <VideoState status={origState} busy={jobState.downloading} />
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .page { padding: 24px 32px 32px; display: flex; flex-direction: column; gap: 18px; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 22px; font-weight: 600; }
  h2 { font-size: 15px; font-weight: 600; }
  .sub { font-size: 13px; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .push { margin-left: auto; }
  .small { font-size: 13px; }
  .stage { position: relative; border-radius: 12px; overflow: hidden; background: var(--inset); border: 1px solid var(--line); }
  .toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .seg button.missing { opacity: 0.45; }
  .found { display: flex; flex-direction: column; gap: 12px; }
  .err { color: var(--bad); }
  .results { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
             gap: 10px; max-height: 340px; overflow: auto; }
  .result { width: 100%; padding: 8px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface-2);
            display: flex; flex-direction: column; gap: 6px; text-align: left; }
  .result:hover { border-color: var(--accent); }
  .result img { width: 100%; height: 96px; object-fit: contain; background: var(--inset); border-radius: 6px; }
  .meta { display: flex; flex-direction: column; gap: 2px; }
  .when { display: flex; align-items: center; gap: 6px; font-size: 13px; }
  video { width: 100%; display: block; background: #000; }
  .tl-wrap { display: flex; flex-direction: column; gap: 6px; }
  .tl-head { display: flex; align-items: center; gap: 12px; }
  .chart { display: grid; grid-template-columns: auto 1fr; column-gap: 8px; row-gap: 6px; }
  .yaxis { position: relative; height: 64px; font-size: 11px; line-height: 1; min-width: 3ch; }
  .yaxis span { position: absolute; right: 0; transform: translateY(50%); }
  /* 64 px box: 1 px borders + 8 px headroom leave PLOT (54 px) for the bars */
  .timeline { position: relative; display: flex; align-items: flex-end; gap: 3px; height: 64px; padding: 8px 3px 0; border-radius: 8px;
              background: var(--rail); border: 1px solid var(--line); box-sizing: border-box; }
  .grid-line { position: absolute; left: 0; right: 0; height: 0; border-top: 1px dashed rgba(255, 255, 255, 0.08); pointer-events: none; }
  .timeline.person { --bar: var(--person); } .timeline.vehicle { --bar: var(--vehicle); } .timeline.animal { --bar: var(--animal); }
  .notice { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 13.5px; }
  .dot.all { background: var(--accent); }
  /* whole column is the click target; the bar is drawn inside it */
  .timeline button { flex: 1; height: 100%; padding: 0; border: 0; background: transparent; position: relative; cursor: crosshair; }
  .timeline button::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: var(--h);
                            border-radius: 2px 2px 0 0; background: var(--bar, var(--accent)); }
  .timeline button:hover { background: rgba(142, 168, 255, 0.08); }
  .timeline button:hover::after { filter: brightness(1.25); }
  .playhead { position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--text); pointer-events: none; }
  .orig { position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--person); pointer-events: none; }
  .axis { display: flex; justify-content: space-between; font-size: 11px; }
  .original { display: flex; flex-direction: column; gap: 12px; }
  .original video { border-radius: 8px; }
  .orig-wrap { position: relative; min-height: 120px; border-radius: 8px; overflow: hidden; background: #000; }
</style>

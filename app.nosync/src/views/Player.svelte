<script>
  // Interim player: plays the rendered synopsis MP4s and jumps to original footage.
  // Clicking the activity graph moves the synopsis to that time of day and loads the original
  // recording from the same moment. The live, in-app compositor replaces the MP4s in phase 3.
  import { onMount } from 'svelte';
  import { api, media } from '../lib/api.js';
  import { clock, longDay, parseClock, duration, CAT_LABEL } from '../lib/format.js';
  import RegionOverlay from '../components/RegionOverlay.svelte';

  let { params } = $props();

  let days = $state([]);
  let day = $state(params.day ?? '');
  let camera = $state(params.camera ?? '');
  let info = $state(null);
  let videoName = $state('');
  let synVideo = $state();
  let length = $state(0); // synopsis seconds that span the 24 h day (from the render's sidecar)
  let synTime = $state(0);
  let originalAt = $state('12:00');
  let originalT = $state(null);
  let originalSrc = $state('');

  // Find in area
  let drawing = $state(false);
  let regions = $state([]);
  let kinds = $state({ person: true, vehicle: true, animal: true });
  let found = $state(null);
  let searching = $state(false);
  let searchError = $state('');

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  const videos = $derived((info?.outputs ?? []).filter((o) => o.name.endsWith('.mp4')));
  const dayT = $derived(length ? Math.min(86399, (synTime / length) * 86400) : null);

  onMount(async () => {
    days = await api.days();
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
    fetch(media.out(day, camera, videoName.replace(/\.mp4$/, '.json')))
      .then((r) => (r.ok ? r.json() : null))
      .then((meta) => { if (meta?.length) length = meta.length; })
      .catch(() => {});
  });

  function onMeta() {
    if (!length && synVideo?.duration) length = synVideo.duration;
  }

  function label(name) {
    // synopsis_cutout_category.mp4 -> "cutout · type"
    const [, style, color, ...rest] = name.replace('.mp4', '').split('_');
    return [style, color === 'category' ? 'type' : color, ...rest].filter(Boolean).join(' · ');
  }

  function bars(hourly) {
    const max = Math.max(1, ...hourly);
    return hourly.map((v) => Math.round((56 * v) / max));
  }

  function toInput(t) {
    return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}`;
  }

  function showOriginal(t) {
    originalT = t;
    originalAt = toInput(t);
    originalSrc = media.clip(day, camera, t, 120);
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
             onloadedmetadata={onMeta} ontimeupdate={() => (synTime = synVideo.currentTime)} onseeked={() => (synTime = synVideo.currentTime)}></video>
      <RegionOverlay bind:regions {drawing} />
    {:else}
      <div class="empty">No synopsis rendered for this camera and day yet. Run it from <a href="#/jobs">Jobs</a>.</div>
    {/if}
  </section>

  <div class="toolbar">
    {#if videos.length > 1}
      <div class="seg" role="group" aria-label="Version">
        {#each videos as v}<button aria-pressed={v.name === videoName} onclick={() => (videoName = v.name)}>{label(v.name)}</button>{/each}
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
        <span class="small muted">Click the graph to jump the synopsis there and show the original footage</span>
        {#if dayT != null}<span class="mono small push">synopsis at {clock(dayT)}</span>{/if}
      </div>
      <div class="timeline">
        {#each bars(info.hourly) as h, i}
          <button style:--h="{Math.max(1, h)}px" aria-label="Jump to {clock(i * 3600)}" title="Jump to {clock(i * 3600)}–{clock((i + 1) * 3600)}"
                  onclick={(e) => onBarClick(e, i)}></button>
        {/each}
        {#if dayT != null}<div class="playhead" style:left="{(dayT / 86400) * 100}%" aria-hidden="true"></div>{/if}
        {#if originalT != null}<div class="orig" style:left="{(originalT / 86400) * 100}%" aria-hidden="true"></div>{/if}
      </div>
      <div class="axis mono dim"><span>12a</span><span>3a</span><span>6a</span><span>9a</span><span>12p</span><span>3p</span><span>6p</span><span>9p</span><span>12a</span></div>
    </section>

    <section class="panel original" aria-label="Original footage">
      <div class="row">
        <h2>Original footage</h2>
        <input type="time" bind:value={originalAt} aria-label="Time of day" />
        <button class="btn" onclick={() => jumpTo(parseClock(originalAt))}>Go</button>
        {#if originalT != null}<span class="small muted">from {clock(originalT, true)}</span>{/if}
      </div>
      {#if originalSrc}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video src={originalSrc} controls autoplay></video>
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
  .timeline { position: relative; display: flex; align-items: flex-end; gap: 3px; height: 64px; padding: 0 3px; border-radius: 8px;
              background: var(--rail); border: 1px solid var(--line); }
  /* whole column is the click target; the bar is drawn inside it */
  .timeline button { flex: 1; height: 100%; padding: 0; border: 0; background: transparent; position: relative; cursor: crosshair; }
  .timeline button::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: var(--h);
                            border-radius: 2px 2px 0 0; background: var(--accent); }
  .timeline button:hover { background: rgba(142, 168, 255, 0.08); }
  .timeline button:hover::after { background: #B7C7FF; }
  .playhead { position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--text); pointer-events: none; }
  .orig { position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--person); pointer-events: none; }
  .axis { display: flex; justify-content: space-between; font-size: 11px; }
  .original { display: flex; flex-direction: column; gap: 12px; }
  .original video { border-radius: 8px; }
</style>

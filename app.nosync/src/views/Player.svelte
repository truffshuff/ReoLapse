<script>
  // Interim player: plays the rendered synopsis MP4s and jumps to original footage.
  // The live, in-app compositor (instant style/filter changes, click an object) replaces this in phase 3.
  import { onMount } from 'svelte';
  import { api, media } from '../lib/api.js';
  import { clock, longDay, parseClock, CAT_LABEL } from '../lib/format.js';

  let { params } = $props();

  let days = $state([]);
  let day = $state(params.day ?? '');
  let camera = $state(params.camera ?? '');
  let info = $state(null);
  let videoName = $state('');
  let originalAt = $state('12:00');
  let originalSrc = $state('');

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  const videos = $derived((info?.outputs ?? []).filter((o) => o.name.endsWith('.mp4')));

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
      const vids = x.outputs.filter((o) => o.name.endsWith('.mp4'));
      videoName = (vids.find((v) => v.name.includes('cutout')) ?? vids[0])?.name ?? '';
      originalSrc = '';
    });
  });

  function label(name) {
    // synopsis_cutout_category.mp4 -> "Cutout · type"
    const [, style, color, ...rest] = name.replace('.mp4', '').split('_');
    return [style, color === 'category' ? 'type' : color, ...rest].filter(Boolean).join(' · ');
  }

  function bars(hourly) {
    const max = Math.max(1, ...hourly);
    return hourly.map((v) => Math.round((56 * v) / max));
  }

  function showOriginal(t = parseClock(originalAt)) {
    originalAt = `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}`;
    originalSrc = media.clip(day, camera, t, 120);
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
      <video src={media.out(day, camera, videoName)} controls autoplay muted></video>
    {:else}
      <div class="empty">No synopsis rendered for this camera and day yet. Run it from <a href="#/jobs">Jobs</a>.</div>
    {/if}
  </section>

  {#if videos.length > 1}
    <div class="seg" role="group" aria-label="Version">
      {#each videos as v}<button aria-pressed={v.name === videoName} onclick={() => (videoName = v.name)}>{label(v.name)}</button>{/each}
    </div>
  {/if}

  {#if info}
    <section aria-label="Activity by hour">
      <div class="timeline">
        {#each bars(info.hourly) as h, i}
          <button style:--h="{Math.max(1, h)}px" aria-label="Original footage at {clock(i * 3600)}" title="Original footage at {clock(i * 3600)}"
                  onclick={() => showOriginal(i * 3600 + 1800)}></button>
        {/each}
      </div>
      <div class="axis mono dim"><span>12a</span><span>3a</span><span>6a</span><span>9a</span><span>12p</span><span>3p</span><span>6p</span><span>9p</span><span>12a</span></div>
    </section>

    <section class="panel original" aria-label="Original footage">
      <div class="row">
        <h2>Original footage</h2>
        <input type="time" bind:value={originalAt} aria-label="Time of day" />
        <button class="btn" onclick={() => showOriginal()}>Show</button>
        <span class="dim small">Click an hour above to jump there.</span>
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
  .stage { border-radius: 12px; overflow: hidden; background: var(--inset); border: 1px solid var(--line); }
  video { width: 100%; display: block; background: #000; }
  .timeline { display: flex; align-items: flex-end; gap: 3px; height: 64px; padding: 0 3px; border-radius: 8px;
              background: var(--rail); border: 1px solid var(--line); }
  /* whole column is the click target; the bar is drawn inside it */
  .timeline button { flex: 1; height: 100%; padding: 0; border: 0; background: transparent; position: relative; }
  .timeline button::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: var(--h);
                            border-radius: 2px 2px 0 0; background: var(--accent); }
  .timeline button:hover { background: rgba(142, 168, 255, 0.08); }
  .timeline button:hover::after { background: #B7C7FF; }
  .axis { display: flex; justify-content: space-between; font-size: 11px; margin-top: 6px; }
  .original { display: flex; flex-direction: column; gap: 12px; }
  .original video { border-radius: 8px; }
</style>

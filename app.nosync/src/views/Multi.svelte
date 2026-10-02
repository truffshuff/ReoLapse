<script>
  // Synced original footage: every camera's real recording from the same second, played together.
  import { onMount } from 'svelte';
  import Icon from '../components/Icon.svelte';
  import { api, media } from '../lib/api.js';
  import { clock, longDay, parseClock } from '../lib/format.js';

  let { params } = $props();

  let days = $state([]);
  let day = $state(params.day ?? '');
  let at = $state(params.t ? '' : '16:15');
  let t = $state(params.t ? Number(params.t) : null);
  let playing = $state(false);
  let videos = $state({});

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  const lanes = $derived(cameras.map((c) => ({ camera: c.camera, hourly: c.hourly, max: Math.max(1, ...c.hourly) })));

  onMount(async () => {
    days = await api.days();
    if (!day || !days.some((d) => d.day === day)) day = days[0]?.day ?? '';
    if (t == null) load();
  });

  function load(seconds = parseClock(at)) {
    t = seconds;
    at = `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')}`;
    playing = false;
  }

  function shift(delta) {
    const base = t + (Object.values(videos).find(Boolean)?.currentTime ?? 0);
    load(Math.max(0, Math.min(86399, base + delta)));
  }

  // Each clip starts at the keyframe before t, so align them on their own start offsets before playing.
  async function playAll() {
    const vs = Object.values(videos).filter(Boolean);
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

  <section class="grid">
    {#each cameras as c (c.camera)}
      <figure>
        {#if t != null}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={videos[c.camera]} src={media.clip(day, c.camera, t, 180)} muted preload="auto"></video>
        {/if}
        <figcaption><span class="tag">{c.camera}</span></figcaption>
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
  .head { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--text); }
</style>

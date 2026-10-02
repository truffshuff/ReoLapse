<script>
  import { onMount } from 'svelte';
  import { api, media } from '../lib/api.js';
  import { longDay } from '../lib/format.js';

  let { params } = $props();

  const VIEWS = [
    { id: 'trails', file: 'trails.jpg', label: 'Trails', caption: 'Every path of the day, colored by when it happened.' },
    { id: 'heatmap', file: 'heatmap.jpg', label: 'Heatmap', caption: 'How many objects passed through each spot; each counts once however long it stayed.' },
    { id: 'group', file: 'strobe.jpg', label: 'Group shot', caption: 'One snapshot of everyone who came by in daylight, far ones behind near ones.' },
  ];

  let days = $state([]);
  let day = $state(params.day ?? '');
  let camera = $state(params.camera ?? '');
  let view = $state('trails');

  const cameras = $derived(days.find((d) => d.day === day)?.cameras ?? []);
  const cam = $derived(cameras.find((c) => c.camera === camera));
  const current = $derived(VIEWS.find((v) => v.id === view));
  const available = $derived(new Set((cam?.outputs ?? []).map((o) => o.name)));

  onMount(async () => {
    days = await api.days();
    if (!day || !days.some((d) => d.day === day)) day = days.find((d) => d.cameras.some((c) => c.outputs.length))?.day ?? days[0]?.day ?? '';
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
      <select bind:value={day} aria-label="Day">{#each days as d}<option value={d.day}>{d.day}</option>{/each}</select>
      <div class="seg" role="group" aria-label="Camera">
        {#each cameras as c}<button aria-pressed={c.camera === camera} onclick={() => (camera = c.camera)}>{c.camera}</button>{/each}
      </div>
    </div>
  </header>

  <div class="seg" role="tablist" aria-label="View">
    {#each VIEWS as v}<button role="tab" aria-selected={v.id === view} onclick={() => (view = v.id)}>{v.label}</button>{/each}
  </div>

  <section class="stage">
    {#if cam && available.has(current.file)}
      <img src={media.out(day, camera, current.file)} alt="{current.label} for {camera}" />
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
  .legend { max-width: 640px; }
  .grad { height: 12px; border-radius: 6px;
          background: linear-gradient(90deg, #6E6EFF 0%, #DC5ADC 25%, #FF9628 37.5%, #FFE632 50%, #96E63C 62.5%, #28D2E6 75%, #3C8CFF 87.5%, #6E6EFF 100%); }
  .axis { display: flex; justify-content: space-between; font-size: 11px; margin-top: 6px; }
  p { margin: 0; font-size: 14px; }
</style>

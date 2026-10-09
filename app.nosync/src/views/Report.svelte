<script>
  // A printable report for one day: a summary, then per camera the activity by hour, the day's
  // pictures and every detection grouped by hour. Options live in the URL, so the hidden window
  // Electron renders to PDF (print=1) shows exactly what the preview shows.
  import { onMount, tick } from 'svelte';
  import { api, media } from '../lib/api.js';
  import { clock, duration, longDay, STREAM_NAME, analyzedFrom, analyzedWith, when } from '../lib/format.js';

  let { params } = $props();

  const TYPES = [['person', 'People', 'person'], ['vehicle', 'Vehicles', 'vehicle'], ['animal', 'Animals', 'animal']];
  const NOUN = { person: ['person', 'people'], vehicle: ['vehicle', 'vehicles'], animal: ['animal', 'animals'] };
  const PER_HOUR = [['all', 'All'], ['24', 'Up to 24'], ['12', 'Up to 12'], ['0', 'None']];
  const print = params.print === '1';

  let day = $state(params.day ?? '');
  let info = $state(null); // the day: { day, cameras: [...] }
  let chosen = $state(params.cams ? params.cams.split(',') : null);
  let types = $state(params.types ? params.types.split(',') : TYPES.map(([k]) => k));
  let perHour = $state(params.per ?? '24');
  let objects = $state({}); // camera -> objects
  let error = $state('');
  let saving = $state('');

  const cams = $derived((info?.cameras ?? []).filter((c) => (chosen ?? info.cameras.map((x) => x.camera)).includes(c.camera)));
  const n = (k, v) => `${v} ${NOUN[k][v === 1 ? 0 : 1]}`;
  const count = (c, k) => c.counts?.[k] ?? 0;

  // Keep the options in the address, for the PDF window.
  $effect(() => {
    if (print || !info) return;
    const q = new URLSearchParams({ day, cams: cams.map((c) => c.camera).join(','), types: types.join(','), per: perHour });
    history.replaceState(null, '', `#/report?${q}`);
  });

  onMount(() => {
    // Paper is white: the app's dark page background would otherwise fill the PDF's margins.
    // (color-scheme too: Chromium paints the PDF's page margins in the scheme's canvas color).
    const root = document.documentElement, was = [root.style.background, document.body.style.background];
    if (print) {
      root.classList.add('printing');
      root.style.background = document.body.style.background = '#fff';
      root.style.colorScheme = 'light';
    }
    load();
    return () => {
      root.classList.remove('printing');
      [root.style.background, document.body.style.background] = was;
      root.style.colorScheme = '';
    };
  });

  async function load() {
    try {
      const days = await api.days();
      info = days.find((d) => d.day === day) ?? days[0];
      day = info?.day ?? '';
      if (!info) throw new Error('no days yet');
      if (chosen == null) chosen = info.cameras.filter((c) => c.analyzed).map((c) => c.camera);
    } catch (e) {
      error = e.message;
      window.__reportReady = `Couldn't load the day: ${error}`;
    }
  }

  // Every detection for the chosen cameras and types.
  $effect(() => {
    if (!info) return;
    const d = day, want = [...types];
    for (const c of cams) {
      if (!c.analyzed) continue;
      api.objects(d, c.camera, [], want)
        .then((r) => (objects = { ...objects, [c.camera]: r.objects.sort((a, b) => a.t0 - b.t0) }))
        .catch(() => (objects = { ...objects, [c.camera]: [] }));
    }
  });

  // print=1: tell Electron when everything, pictures included, has loaded.
  $effect(() => {
    if (!print || !info || cams.some((c) => c.analyzed && !objects[c.camera])) return;
    tick().then(async () => {
      await Promise.all([...document.images].map((img) => img.complete ? null
        : new Promise((r) => { img.onload = img.onerror = r; })));
      window.__reportReady = true;
    });
  });

  function byHour(list) {
    const hours = new Map();
    for (const o of list ?? []) {
      const h = Math.min(23, Math.floor(o.t0 / 3600));
      if (!hours.has(h)) hours.set(h, []);
      hours.get(h).push(o);
    }
    return [...hours.entries()].sort((a, b) => a[0] - b[0]);
  }

  const hourName = (h) => `${clock(h * 3600).replace(':00', '')} – ${clock(((h + 1) % 24) * 3600).replace(':00', '')}`;
  const shown = (list) => (perHour === 'all' ? list : list.slice(0, Number(perHour)));

  function hourCounts(list) {
    const c = { person: 0, vehicle: 0, animal: 0 };
    for (const o of list) c[o.category]++;
    return TYPES.map(([k]) => k).filter((k) => c[k]).map((k) => n(k, c[k])).join(' · ');
  }

  // ------------------------------------------------------------ chart
  const W = 720, H = 150, L = 34, B = 20, T = 8;
  const plotW = W - L, plotH = H - B - T, slot = plotW / 24, barW = slot - 6;

  function niceMax(m) {
    if (m <= 2) return 2;
    const p = 10 ** Math.floor(Math.log10(m));
    return [1, 2, 4, 6, 8, 10].map((f) => f * p).find((v) => v >= m);
  }

  function chart(c) {
    const by = c.hourly_by ?? {};
    const ks = TYPES.map(([k]) => k).filter((k) => types.includes(k));
    const totals = Array.from({ length: 24 }, (_, h) => ks.reduce((s, k) => s + (by[k]?.[h] ?? 0), 0));
    const top = niceMax(Math.max(0, ...totals));
    const y = (v) => T + plotH - (plotH * v) / top;
    const bars = [];
    for (let h = 0; h < 24; h++) {
      let base = 0;
      const stack = ks.map((k) => [k, by[k]?.[h] ?? 0]).filter(([, v]) => v);
      stack.forEach(([k, v], i) => {
        const y0 = y(base), y1 = y(base + v);
        const gap = i ? 2 : 0; // 2 px of paper between stacked segments
        bars.push({ k, h, v, x: L + h * slot + 3, y: y1, hgt: Math.max(1, y0 - y1 - gap), top: i === stack.length - 1 });
        base += v;
      });
    }
    return { top, y, bars, totals };
  }

  // Rectangle with rounded top corners only (the end of the bar), anchored to the baseline.
  function barPath(b) {
    const r = b.top ? Math.min(3, b.hgt, barW / 2) : 0;
    const { x, y, hgt } = b, w = barW;
    return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
  }

  // ------------------------------------------------------------ pictures
  const has = (c, name) => (c.outputs ?? []).some((o) => o.name === name);
  function still(c, base) {
    const one = types.length === 1 ? `${base}_${types[0]}.jpg` : null;
    return one && has(c, one) ? one : has(c, `${base}.jpg`) ? `${base}.jpg` : null;
  }

  async function savePdf() {
    saving = 'Preparing the PDF…';
    try {
      const res = await window.reolapse.savePdf(location.hash, `Reolapse ${day}.pdf`);
      saving = res?.saved ? `Saved to ${res.path}` : '';
    } catch (e) {
      saving = `Couldn't make the PDF: ${e.message}`;
    }
  }

  function toggle(list, k) {
    return list.includes(k) ? list.filter((x) => x !== k) : [...list, k];
  }
</script>

{#if !print}
  <div class="controls">
    <a class="btn quiet" href={`#/today`}>← Back</a>
    <div class="seg" role="group" aria-label="Cameras">
      {#each info?.cameras ?? [] as c}
        <button aria-pressed={cams.some((x) => x.camera === c.camera)} disabled={!c.analyzed}
                onclick={() => (chosen = toggle(chosen ?? [], c.camera))}>{c.camera}</button>
      {/each}
    </div>
    <div class="seg" role="group" aria-label="Types">
      {#each TYPES as [k, label]}
        <button aria-pressed={types.includes(k)} onclick={() => { const t = toggle(types, k); if (t.length) types = t; }}>
          <span class="dot {k}"></span> {label}</button>
      {/each}
    </div>
    <label class="per">Pictures per hour
      <select bind:value={perHour}>{#each PER_HOUR as [v, label]}<option value={v}>{label}</option>{/each}</select>
    </label>
    <span class="push"></span>
    {#if saving}<span class="small muted">{saving}</span>{/if}
    {#if window.reolapse?.savePdf}
      <button class="btn primary" onclick={savePdf} disabled={saving === 'Preparing the PDF…'}>Save PDF</button>
    {:else}
      <button class="btn primary" onclick={() => window.print()}>Print…</button>
    {/if}
  </div>
{/if}

<div class="paper" class:print>
  {#if error}
    <p>Couldn't load the report: {error}</p>
  {:else if info}
    <section class="cover">
      <div class="brand">Reolapse</div>
      <h1>{longDay(day)}</h1>
      <p class="sub">{new Date(day + 'T12:00:00').getFullYear()} · {cams.length} {cams.length === 1 ? 'camera' : 'cameras'}
        · {TYPES.filter(([k]) => types.includes(k)).map(([, l]) => l.toLowerCase()).join(', ')}</p>
      <table class="summary">
        <thead><tr><th>Camera</th>{#each TYPES.filter(([k]) => types.includes(k)) as [k, l]}<th class="num">{l}</th>{/each}<th>Footage</th><th>Objects found with</th></tr></thead>
        <tbody>
          {#each cams as c}
            <tr>
              <td>{c.camera}</td>
              {#each TYPES.filter(([k]) => types.includes(k)) as [k]}<td class="num">{count(c, k)}</td>{/each}
              <td>{STREAM_NAME[c.footage?.stream] ?? '–'}</td>
              <td>{c.analysis?.model ? analyzedWith(c.analysis) : c.analysis?.stream ? `${analyzedFrom(c.analysis)} (settings not recorded)` : '–'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      <p class="note">Objects are people, vehicles and animals that moved; each is counted once, at the hour it
        appeared. Generated {when(new Date().toISOString())}.</p>
    </section>

    {#each cams as c (c.camera)}
      {@const ch = chart(c)}
      <section class="camera">
        <h2>{c.camera}</h2>
        <p class="sub">{TYPES.filter(([k]) => types.includes(k)).map(([k]) => n(k, count(c, k))).join(' · ')}
          {#if c.analysis}· {analyzedFrom(c.analysis)}{c.analysis.model ? ` · ${analyzedWith(c.analysis)}` : ''}{/if}</p>

        <figure class="chart">
          <figcaption>
            <span>Objects per hour</span>
            <span class="legend">{#each TYPES.filter(([k]) => types.includes(k)) as [k, l]}<span><i class="sw {k}"></i>{l}</span>{/each}</span>
          </figcaption>
          <svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Objects per hour for {c.camera}">
            {#each [0, ch.top / 2, ch.top] as v}
              <line x1={L} x2={W} y1={ch.y(v)} y2={ch.y(v)} class:base={v === 0} class="grid" />
              <text x={L - 6} y={ch.y(v) + 3.5} class="tick" text-anchor="end">{v}</text>
            {/each}
            {#each ch.bars as b}<path d={barPath(b)} class="bar {b.k}"><title>{hourName(b.h)}: {n(b.k, b.v)}</title></path>{/each}
            {#each [0, 3, 6, 9, 12, 15, 18, 21] as h}
              <text x={L + h * slot + slot / 2} y={H - 5} class="tick" text-anchor="middle">{clock(h * 3600).replace(':00', '').replace(' ', '').toLowerCase()}</text>
            {/each}
          </svg>
        </figure>

        <div class="pictures">
          {#if c.backgrounds}
            <figure><img src={media.bg(day, c.camera, 72, 1600)} alt="{c.camera} at midday" loading={print ? 'eager' : 'lazy'} /><figcaption>The scene at midday</figcaption></figure>
          {/if}
          {#each [['trails', 'Trails: every path, colored by time of day'], ['heatmap', 'Heatmap: where things moved most'], ['strobe', 'Group shot: one picture of each object seen in daylight']] as [base, cap]}
            {@const f = still(c, base)}
            {#if f}<figure><img src={media.out(day, c.camera, f, 1600)} alt={cap} loading={print ? 'eager' : 'lazy'} /><figcaption>{cap}</figcaption></figure>{/if}
          {/each}
        </div>

        <h3>Detections by hour</h3>
        {#if !objects[c.camera]}
          <p class="note">Loading…</p>
        {:else if !objects[c.camera].length}
          <p class="note">Nothing of these types moved on this camera.</p>
        {/if}
        {#each byHour(objects[c.camera]) as [h, list]}
          <div class="hour">
            <h4>{hourName(h)} <span>· {hourCounts(list)}{perHour !== 'all' && list.length > Number(perHour) ? ` · showing ${perHour === '0' ? 'none' : `the first ${perHour}`}` : ''}</span></h4>
            {#if perHour !== '0'}
              <ul class="thumbs">
                {#each shown(list) as o (o.id)}
                  <li>
                    <img src={media.thumb(day, c.camera, o.id, 240)} alt="{NOUN[o.category][0]} at {clock(o.t0)}" loading={print ? 'eager' : 'lazy'} />
                    <span class="t"><i class="sw {o.category}"></i>{clock(o.t0, true)}</span>
                    <span class="d">{NOUN[o.category][0]} · {duration(o.t1 - o.t0)}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/each}
      </section>
    {/each}
  {:else}
    <p class="note">Loading…</p>
  {/if}
</div>

<style>
  /* Paper colors (not the app's dark theme): the category hues are darker steps of the app's,
     validated for contrast and color-blind separation on white. */
  .paper { --ink: #1B1D21; --ink-2: #55585F; --ink-3: #85888E; --rule: #E3E3E0; --wash: #F4F4F2;
           --c-person: #D9730D; --c-vehicle: #1C87C9; --c-animal: #2E9E54;
           background: #FFFFFF; color: var(--ink); width: 7.5in; margin: 16px auto 48px; padding: 0.5in;
           box-shadow: 0 6px 30px rgba(0, 0, 0, 0.4); font-size: 11px; line-height: 1.4; }
  .paper.print { width: auto; margin: 0; padding: 0; box-shadow: none; }
  :global(html.printing), :global(html.printing body) { background: #fff !important; height: auto; overflow: visible; }
  @page { size: Letter; margin: 0.5in 0.5in 0.6in; }

  .controls { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
              padding: 14px 24px; background: var(--bg); border-bottom: 1px solid var(--line); }
  .per { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }
  .push { margin-left: auto; }

  h1 { font-size: 26px; margin: 4px 0 2px; font-weight: 650; }
  h2 { font-size: 20px; margin: 0 0 2px; font-weight: 650; }
  h3 { font-size: 13px; margin: 18px 0 6px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-2); }
  h4 { font-size: 12px; margin: 12px 0 6px; font-weight: 600; break-after: avoid; }
  h4 span { font-weight: 400; color: var(--ink-2); }
  .brand { font-size: 12px; font-weight: 600; color: var(--c-vehicle); letter-spacing: 0.04em; }
  .sub { margin: 0 0 12px; color: var(--ink-2); }
  .note { color: var(--ink-3); }

  .summary { width: 100%; border-collapse: collapse; margin: 12px 0; }
  .summary th, .summary td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--rule); }
  .summary th { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ink-2); font-weight: 600; }
  .summary .num { text-align: right; font-variant-numeric: tabular-nums; }

  .camera { break-before: page; }
  .print .camera { padding-top: 0; }
  .paper:not(.print) .camera { margin-top: 36px; padding-top: 24px; border-top: 2px dashed var(--rule); }

  .chart { margin: 8px 0 4px; }
  .chart figcaption { display: flex; justify-content: space-between; font-weight: 600; margin-bottom: 2px; }
  .legend { display: flex; gap: 12px; font-weight: 400; color: var(--ink-2); }
  .legend span { display: inline-flex; align-items: center; gap: 4px; }
  .grid { stroke: var(--rule); stroke-width: 1; }
  .grid.base { stroke: var(--ink-3); }
  .tick { font-size: 9px; fill: var(--ink-3); font-variant-numeric: tabular-nums; }
  .bar.person { fill: var(--c-person); } .bar.vehicle { fill: var(--c-vehicle); } .bar.animal { fill: var(--c-animal); }

  i.sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px; flex: none; }
  i.sw.person { background: var(--c-person); } i.sw.vehicle { background: var(--c-vehicle); } i.sw.animal { background: var(--c-animal); }

  .pictures { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
  .pictures figure { margin: 0; break-inside: avoid; }
  .pictures img { width: 100%; display: block; border-radius: 4px; }
  .pictures figcaption { color: var(--ink-2); margin-top: 3px; }

  .hour { break-inside: auto; }
  .thumbs { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; }
  .thumbs li { break-inside: avoid; display: flex; flex-direction: column; gap: 1px; }
  .thumbs img { width: 100%; height: 78px; object-fit: contain; background: var(--wash); border-radius: 3px; }
  .thumbs .t { display: flex; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; font-size: 10px; }
  .thumbs .d { color: var(--ink-3); font-size: 9.5px; }
</style>

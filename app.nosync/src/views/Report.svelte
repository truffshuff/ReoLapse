<script>
  // A printable report for one day: a summary, then per camera the activity by hour, the day's
  // pictures and every detection grouped by hour, and a section for each named area drawn on that
  // camera. Options live in the URL and areas in Settings, so the hidden window Electron renders to
  // PDF (print=1) shows exactly what the preview shows.
  import { onMount, tick } from 'svelte';
  import { api, media } from '../lib/api.js';
  import { clock, duration, longDay, STREAM_NAME, analyzedFrom, analyzedWith, when } from '../lib/format.js';
  import RegionOverlay from '../components/RegionOverlay.svelte';

  let { params } = $props();

  const TYPES = [['person', 'People'], ['vehicle', 'Vehicles'], ['animal', 'Animals']];
  const NOUN = { person: ['person', 'people'], vehicle: ['vehicle', 'vehicles'], animal: ['animal', 'animals'] };
  const PER_HOUR = [['all', 'All'], ['96', 'Up to 96'], ['48', 'Up to 48'], ['24', 'Up to 24'], ['12', 'Up to 12'],
                    ['6', 'Up to 6'], ['0', 'None (counts only)']];
  const SCENE_SLOT = 72; // the 12:00 background
  const print = params.print === '1';

  let day = $state(params.day ?? '');
  let info = $state(null); // the day: { day, cameras: [...] }
  let chosen = $state(params.cams ? params.cams.split(',') : null);
  let types = $state(params.types ? params.types.split(',') : TYPES.map(([k]) => k));
  let perHour = $state(params.per ?? '24');
  let withAreas = $state(params.ar !== '0');
  let areas = $state({}); // Settings: camera -> [{ name, boxes }]
  let objects = $state({}); // camera -> objects
  let inArea = $state({}); // "camera|index|name" -> objects
  let editing = $state(null); // { camera, name, boxes } while drawing a new area
  let error = $state('');
  let saving = $state('');

  const cams = $derived((info?.cameras ?? []).filter((c) => (chosen ?? info.cameras.map((x) => x.camera)).includes(c.camera)));
  const shownTypes = $derived(TYPES.filter(([k]) => types.includes(k)));
  const n = (k, v) => `${v} ${NOUN[k][v === 1 ? 0 : 1]}`;
  const areasOf = (camera) => (withAreas ? areas[camera] ?? [] : []);
  const areaKey = (camera, i, a) => `${camera}|${i}|${a.name}`;

  // Keep the options in the address, for the PDF window.
  $effect(() => {
    if (print || !info) return;
    const q = new URLSearchParams({ day, cams: cams.map((c) => c.camera).join(','), types: types.join(','),
                                    per: perHour, ar: withAreas ? '1' : '0' });
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
      const [days, s] = await Promise.all([api.days(), api.settings()]);
      areas = s.areas ?? {};
      info = days.find((d) => d.day === day) ?? days[0];
      day = info?.day ?? '';
      if (!info) throw new Error('no days yet');
      if (chosen == null) chosen = info.cameras.filter((c) => c.analyzed).map((c) => c.camera);
    } catch (e) {
      error = e.message;
      window.__reportReady = `Couldn't load the day: ${error}`;
    }
  }

  // Every detection for the chosen cameras and types, and those that entered each area.
  $effect(() => {
    if (!info) return;
    const d = day, want = [...types];
    for (const c of cams) {
      if (!c.analyzed) continue;
      api.objects(d, c.camera, [], want)
        .then((r) => (objects = { ...objects, [c.camera]: r.objects.sort((a, b) => a.t0 - b.t0) }))
        .catch(() => (objects = { ...objects, [c.camera]: [] }));
      areasOf(c.camera).forEach((a, i) => {
        const key = areaKey(c.camera, i, a);
        api.objects(d, c.camera, a.boxes, want)
          .then((r) => (inArea = { ...inArea, [key]: r.objects.sort((x, y) => x.enter - y.enter) }))
          .catch(() => (inArea = { ...inArea, [key]: [] }));
      });
    }
  });

  // print=1: tell Electron when everything, pictures included, has loaded.
  $effect(() => {
    if (!print || !info) return;
    const pending = cams.some((c) => c.analyzed && (!objects[c.camera]
      || areasOf(c.camera).some((a, i) => !inArea[areaKey(c.camera, i, a)])));
    if (pending) return;
    tick().then(async () => {
      await Promise.all([...document.images].map((img) => img.complete ? null
        : new Promise((r) => { img.onload = img.onerror = r; })));
      window.__reportReady = true;
    });
  });

  // ------------------------------------------------------------ areas
  async function saveAreas(next) {
    const before = areas;
    areas = next;
    try {
      await api.saveSettings({ areas: next });
    } catch (e) {
      areas = before;
      saving = `Couldn't save the area: ${e.message}`;
    }
  }

  function startArea(camera) {
    editing = { camera, name: `Area ${(areas[camera]?.length ?? 0) + 1}`, boxes: [] };
  }

  async function finishArea() {
    const { camera, name, boxes } = $state.snapshot(editing);
    if (!boxes.length || !name.trim()) return;
    await saveAreas({ ...areas, [camera]: [...(areas[camera] ?? []), { name: name.trim().slice(0, 60), boxes }] });
    editing = null;
  }

  function removeArea(camera, i) {
    const left = (areas[camera] ?? []).filter((_, j) => j !== i);
    const next = { ...areas };
    if (left.length) next[camera] = left; else delete next[camera];
    saveAreas(next);
  }

  // ------------------------------------------------------------ lists
  function byHour(list, key) {
    const hours = new Map();
    for (const o of list ?? []) {
      const h = Math.min(23, Math.floor(o[key] / 3600));
      if (!hours.has(h)) hours.set(h, []);
      hours.get(h).push(o);
    }
    return [...hours.entries()].sort((a, b) => a[0] - b[0]);
  }

  const hourName = (h) => `${clock(h * 3600).replace(':00', '')} – ${clock(((h + 1) % 24) * 3600).replace(':00', '')}`;
  const shown = (list) => (perHour === 'all' ? list : list.slice(0, Number(perHour)));

  function countBy(list) {
    const c = { person: 0, vehicle: 0, animal: 0 };
    for (const o of list ?? []) c[o.category]++;
    return c;
  }
  function countText(list) {
    const c = countBy(list);
    return shownTypes.filter(([k]) => c[k]).map(([k]) => n(k, c[k])).join(' · ');
  }

  // Per type, objects in each hour, from a list (areas) in the same shape as the day's hourly_by.
  function hourlyFrom(list, key) {
    const by = { person: Array(24).fill(0), vehicle: Array(24).fill(0), animal: Array(24).fill(0) };
    for (const o of list ?? []) by[o.category][Math.min(23, Math.floor(o[key] / 3600))]++;
    return by;
  }

  // ------------------------------------------------------------ chart
  const W = 720, H = 150, L = 34, B = 20, T = 8;
  const plotW = W - L, plotH = H - B - T, slot = plotW / 24, barW = slot - 6;

  function niceMax(m) {
    if (m <= 2) return 2;
    const p = 10 ** Math.floor(Math.log10(m));
    return [1, 2, 4, 6, 8, 10].map((f) => f * p).find((v) => v >= m);
  }

  function chart(by) {
    const ks = shownTypes.map(([k]) => k);
    const totals = Array.from({ length: 24 }, (_, h) => ks.reduce((s, k) => s + (by?.[k]?.[h] ?? 0), 0));
    const top = niceMax(Math.max(0, ...totals));
    const y = (v) => T + plotH - (plotH * v) / top;
    const bars = [];
    for (let h = 0; h < 24; h++) {
      let base = 0;
      const stack = ks.map((k) => [k, by?.[k]?.[h] ?? 0]).filter(([, v]) => v);
      stack.forEach(([k, v], i) => {
        const y0 = y(base), y1 = y(base + v);
        const gap = i ? 2 : 0; // 2 px of paper between stacked segments
        bars.push({ k, h, v, x: L + h * slot + 3, y: y1, hgt: Math.max(1, y0 - y1 - gap), top: i === stack.length - 1 });
        base += v;
      });
    }
    return { top, y, bars };
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
  const loading = print ? 'eager' : 'lazy';
  const boxStyle = (b) => `left:${b.x0 * 100}%;top:${b.y0 * 100}%;width:${(b.x1 - b.x0) * 100}%;height:${(b.y1 - b.y0) * 100}%`;

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

{#snippet hourChart(by, label)}
  {@const ch = chart(by)}
  <figure class="chart">
    <figcaption>
      <span>Objects per hour</span>
      <span class="legend">{#each shownTypes as [k, l]}<span><i class="sw {k}"></i>{l}</span>{/each}</span>
    </figcaption>
    <svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Objects per hour, {label}">
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
{/snippet}

{#snippet detections(list, camera, key, verb)}
  {#if !list}
    <p class="note">Loading…</p>
  {:else if !list.length}
    <p class="note">Nothing of these types {verb}.</p>
  {/if}
  {#each byHour(list, key) as [h, hourList]}
    <div class="hour">
      <h4>{hourName(h)} <span>· {countText(hourList)}{perHour !== 'all' && hourList.length > Number(perHour)
        ? ` · ${perHour === '0' ? 'cutouts not included' : `showing the first ${perHour}`}` : ''}</span></h4>
      {#if perHour !== '0'}
        <ul class="thumbs">
          {#each shown(hourList) as o (o.id)}
            <li>
              <img src={media.thumb(day, camera, o.id, 240)} alt="{NOUN[o.category][0]} at {clock(o[key])}" {loading} />
              <span class="t"><i class="sw {o.category}"></i>{clock(o[key], true)}</span>
              <span class="d">{NOUN[o.category][0]} · {duration(o.t1 - o.t0)}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/each}
{/snippet}

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
    <label class="per">Cutouts per hour
      <select bind:value={perHour}>{#each PER_HOUR as [v, label]}<option value={v}>{label}</option>{/each}</select>
    </label>
    <label class="per"><input type="checkbox" bind:checked={withAreas} /> Area sections</label>
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
        · {shownTypes.map(([, l]) => l.toLowerCase()).join(', ')}</p>
      <table class="summary">
        <thead><tr><th>Camera</th>{#each shownTypes as [, l]}<th class="num">{l}</th>{/each}<th>Footage</th><th>Objects found with</th></tr></thead>
        <tbody>
          {#each cams as c}
            <tr>
              <td>{c.camera}</td>
              {#each shownTypes as [k]}<td class="num">{c.counts?.[k] ?? 0}</td>{/each}
              <td>{STREAM_NAME[c.footage?.stream] ?? '–'}</td>
              <td>{c.analysis?.model ? analyzedWith(c.analysis) : c.analysis?.stream ? `${analyzedFrom(c.analysis)} (settings not recorded)` : '–'}</td>
            </tr>
            {#each areasOf(c.camera) as a, i}
              {@const list = inArea[areaKey(c.camera, i, a)]}
              {@const cnt = countBy(list)}
              <tr class="area-row">
                <td>↳ {a.name}</td>
                {#each shownTypes as [k]}<td class="num">{list ? cnt[k] : '…'}</td>{/each}
                <td colspan="2">area on {c.camera}</td>
              </tr>
            {/each}
          {/each}
        </tbody>
      </table>
      <p class="note">Objects are people, vehicles and animals that moved; each is counted once, at the hour it
        appeared. An area counts the objects whose center passed through it, at the time they entered.
        Generated {when(new Date().toISOString())}.</p>
    </section>

    {#each cams as c (c.camera)}
      <section class="camera">
        <h2>{c.camera}</h2>
        <p class="sub">{shownTypes.map(([k]) => n(k, c.counts?.[k] ?? 0)).join(' · ')}
          {#if c.analysis}· {analyzedFrom(c.analysis)}{c.analysis.model ? ` · ${analyzedWith(c.analysis)}` : ''}{/if}</p>

        {@render hourChart(c.hourly_by, c.camera)}

        <div class="pictures">
          {#if c.backgrounds}
            <figure><img src={media.bg(day, c.camera, SCENE_SLOT, 1600)} alt="{c.camera} at midday" {loading} /><figcaption>The scene at midday</figcaption></figure>
          {/if}
          {#each [['trails', 'Trails: every path, colored by time of day'], ['heatmap', 'Heatmap: where things moved most'], ['strobe', 'Group shot: one picture of each object seen in daylight']] as [base, cap]}
            {@const f = still(c, base)}
            {#if f}<figure><img src={media.out(day, c.camera, f, 1600)} alt={cap} {loading} /><figcaption>{cap}</figcaption></figure>{/if}
          {/each}
        </div>

        {#if !print}
          <div class="areas-edit">
            <h3>Areas on {c.camera}</h3>
            {#if !c.backgrounds}
              <p class="note">Areas are drawn on the camera's picture, which this day doesn't have yet.</p>
            {:else if editing?.camera === c.camera}
              <p class="note">Drag on the picture to draw the area; add several boxes to one area if you like.</p>
              <div class="draw">
                <img src={media.bg(day, c.camera, SCENE_SLOT, 1600)} alt="Draw an area on {c.camera}" />
                <RegionOverlay bind:regions={editing.boxes} drawing={true} />
              </div>
              <div class="row">
                <input type="text" bind:value={editing.name} maxlength="60" aria-label="Area name" placeholder="Name, e.g. Driveway" />
                <button class="btn primary" onclick={finishArea} disabled={!editing.boxes.length || !editing.name.trim()}>Save area</button>
                <button class="btn quiet" onclick={() => (editing = null)}>Cancel</button>
              </div>
            {:else}
              <div class="row">
                {#each areas[c.camera] ?? [] as a, i}
                  <span class="chip">{a.name}<button aria-label="Delete area {a.name}" onclick={() => removeArea(c.camera, i)}>×</button></span>
                {:else}
                  <span class="note">No areas yet. Each area gets its own section: its counts, objects per hour and detections.</span>
                {/each}
                <button class="btn" onclick={() => startArea(c.camera)} disabled={editing != null}>Draw an area</button>
              </div>
              {#if (areas[c.camera]?.length ?? 0) && !withAreas}<p class="note">Area sections are switched off above.</p>{/if}
            {/if}
            <p class="note">Areas are kept for this camera and used in every day's report. This box isn't printed.</p>
          </div>
        {/if}

        <h3>Detections by hour</h3>
        {@render detections(objects[c.camera], c.camera, 't0', 'moved on this camera')}
      </section>

      {#each areasOf(c.camera) as a, i (areaKey(c.camera, i, a))}
        {@const list = inArea[areaKey(c.camera, i, a)]}
        <section class="camera area">
          <h2>{c.camera} · {a.name}</h2>
          <p class="sub">{list ? (countText(list) || 'nothing of these types') : 'Loading…'} · objects whose center passed through
            {a.boxes.length === 1 ? 'the outlined area' : `the ${a.boxes.length} outlined boxes`}</p>
          {#if c.backgrounds}
            <figure class="scene">
              <div class="boxed">
                <img src={media.bg(day, c.camera, SCENE_SLOT, 1600)} alt="{a.name} outlined on {c.camera}" {loading} />
                {#each a.boxes as b}<div class="box" style={boxStyle(b)}></div>{/each}
              </div>
            </figure>
          {/if}
          {@render hourChart(hourlyFrom(list, 'enter'), `${c.camera} ${a.name}`)}
          <h3>Detections by hour</h3>
          {@render detections(list, c.camera, 'enter', 'passed through this area')}
        </section>
      {/each}
    {/each}
  {:else}
    <p class="note">Loading…</p>
  {/if}
</div>

<style>
  /* Paper colors (not the app's dark theme): the category hues are darker steps of the app's,
     validated for contrast and color-blind separation on white. */
  .paper { --ink: #1B1D21; --ink-2: #55585F; --ink-3: #85888E; --rule: #E3E3E0; --wash: #F4F4F2;
           --c-person: #D9730D; --c-vehicle: #1C87C9; --c-animal: #2E9E54; --c-area: #7A3FD1;
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
  .summary .area-row td { color: var(--ink-2); padding-top: 3px; padding-bottom: 3px; }

  .camera { break-before: page; }
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
  .pictures figure, .scene { margin: 0; break-inside: avoid; }
  .pictures img { width: 100%; display: block; border-radius: 4px; }
  .pictures figcaption { color: var(--ink-2); margin-top: 3px; }

  /* An area: its boxes drawn on the scene (outline + light wash, so it reads in print too). */
  .boxed { position: relative; }
  .boxed img { width: 100%; display: block; border-radius: 4px; }
  .box { position: absolute; border: 2.5px solid var(--c-area); background: rgba(122, 63, 209, 0.14); border-radius: 3px;
         box-shadow: 0 0 0 1.5px rgba(255, 255, 255, 0.85); }

  .areas-edit { margin-top: 14px; padding: 12px; border: 1px dashed var(--rule); border-radius: 8px; }
  .areas-edit h3 { margin-top: 0; }
  .areas-edit .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .areas-edit input[type=text] { flex: 1; min-width: 160px; }
  .draw { position: relative; }
  .draw img { width: 100%; display: block; border-radius: 4px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 4px 4px 10px; border-radius: 14px;
          background: var(--wash); border: 1px solid var(--rule); color: var(--ink); }
  .chip button { border: 0; background: transparent; color: var(--ink-2); font-size: 15px; line-height: 1; padding: 0 6px; cursor: pointer; }

  .hour { break-inside: auto; }
  .thumbs { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; }
  .thumbs li { break-inside: avoid; display: flex; flex-direction: column; gap: 1px; }
  .thumbs img { width: 100%; height: 78px; object-fit: contain; background: var(--wash); border-radius: 3px; }
  .thumbs .t { display: flex; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; font-size: 10px; }
  .thumbs .d { color: var(--ink-3); font-size: 9.5px; }
</style>

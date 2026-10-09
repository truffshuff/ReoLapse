<script>
  // Search across days: objects by date range, time of day (which may wrap past midnight), camera,
  // type, a saved report area and how long they were on camera. Click one to open it in the Player.
  import { onMount } from 'svelte';
  import { api, media } from '../lib/api.js';
  import { clock, duration, shortDay, yesterday } from '../lib/format.js';
  import CorrectMenu from '../components/CorrectMenu.svelte';

  let { params } = $props();

  const TYPES = [['person', 'People'], ['vehicle', 'Vehicles'], ['animal', 'Animals']];
  const MIN = [[0, 'Any length'], [5, '5 s or more'], [30, '30 s or more'], [60, '1 min or more'], [300, '5 min or more']];
  const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); };

  let start = $state(params.start ?? daysAgo(7));
  let end = $state(params.end ?? yesterday());
  let after = $state(params.after ?? '');
  let before = $state(params.before ?? '');
  let cameras = $state([]);
  let chosen = $state([]);
  let types = $state(params.types ? params.types.split(',') : TYPES.map(([k]) => k));
  let area = $state(params.area ?? '');
  let minSeconds = $state(Number(params.min ?? 0));
  let areas = $state([]); // [{ key: "camera|i", label }]
  let result = $state(null);
  let busy = $state(false);
  let error = $state('');

  onMount(async () => {
    const s = await api.settings();
    cameras = s.cameras;
    chosen = params.cams ? params.cams.split(',') : [...s.cameras];
    areas = Object.entries(s.areas ?? {}).flatMap(([c, list]) => list.map((a, i) => ({ key: `${c}|${i}`, label: `${c} · ${a.name}` })));
  });

  // Search whenever a choice changes (debounced), and keep the choices in the address.
  $effect(() => {
    const q = { start, end, after, before, cameras: chosen.join(','), types: types.join(','), area, min_seconds: String(minSeconds) };
    if (!chosen.length && !area) return;
    history.replaceState(null, '', `#/search?${new URLSearchParams({ start, end, after, before, cams: chosen.join(','), types: types.join(','), area, min: String(minSeconds) })}`);
    const timer = setTimeout(async () => {
      busy = true;
      error = '';
      try { result = await api.search(q); } catch (e) { error = e.message; }
      busy = false;
    }, 300);
    return () => clearTimeout(timer);
  });

  const groups = $derived.by(() => {
    const by = new Map();
    for (const o of result?.objects ?? []) {
      if (!by.has(o.day)) by.set(o.day, []);
      by.get(o.day).push(o);
    }
    return [...by.entries()];
  });

  const toggle = (list, k) => (list.includes(k) ? list.filter((x) => x !== k) : [...list, k]);
  const openIn = (o) => `#/player?day=${o.day}&camera=${encodeURIComponent(o.camera)}&t=${Math.floor(o.enter)}`;

  function preset(kind) {
    if (kind === 'night') { after = '22:00'; before = '05:00'; }
    else if (kind === 'day') { after = '07:00'; before = '19:00'; }
    else { after = ''; before = ''; }
  }

  function fixed(o, value) {
    const list = result.objects;
    result = { ...result, objects: value === 'hidden' ? list.filter((x) => !(x.id === o.id && x.day === o.day && x.camera === o.camera))
      : list.map((x) => (x === o ? { ...x, category: value ?? x.category, corrected: value != null } : x)) };
  }
</script>

<div class="page">
  <header>
    <div>
      <h1>Search</h1>
      <div class="muted sub">Find people, vehicles and animals across days</div>
    </div>
  </header>

  <section class="panel form" aria-label="Search">
    <div class="field"><label for="s-from">From</label><input id="s-from" type="date" bind:value={start} /></div>
    <div class="field"><label for="s-to">To</label><input id="s-to" type="date" bind:value={end} /></div>
    <div class="field time">
      <span class="lbl">Time of day</span>
      <div class="row tight">
        <input type="time" bind:value={after} aria-label="After" /><span class="muted">to</span><input type="time" bind:value={before} aria-label="Before" />
      </div>
      <div class="row tight presets">
        <button class="btn quiet mini" onclick={() => preset('any')}>Any time</button>
        <button class="btn quiet mini" onclick={() => preset('night')}>Night (10 PM–5 AM)</button>
        <button class="btn quiet mini" onclick={() => preset('day')}>Daytime</button>
      </div>
    </div>
    <div class="field">
      <label for="s-area">Area</label>
      <select id="s-area" bind:value={area}>
        <option value="">Anywhere in the picture</option>
        {#each areas as a}<option value={a.key}>{a.label}</option>{/each}
      </select>
      {#if !areas.length}<span class="small dim">Save areas in a report or with Find in area in the Player.</span>{/if}
    </div>
    <div class="field"><label for="s-min">On camera for</label>
      <select id="s-min" bind:value={minSeconds}>{#each MIN as [v, l]}<option value={v}>{l}</option>{/each}</select></div>
    <div class="field wide">
      <span class="lbl">Cameras {area ? '(the area decides)' : ''}</span>
      <div class="seg" role="group" aria-label="Cameras">
        {#each cameras as c}<button aria-pressed={chosen.includes(c)} disabled={!!area} onclick={() => (chosen = toggle(chosen, c))}>{c}</button>{/each}
      </div>
    </div>
    <div class="field wide">
      <span class="lbl">Types</span>
      <div class="seg" role="group" aria-label="Types">
        {#each TYPES as [k, l]}<button aria-pressed={types.includes(k)} onclick={() => { const t = toggle(types, k); if (t.length) types = t; }}><span class="dot {k}"></span> {l}</button>{/each}
      </div>
    </div>
  </section>

  <div class="row">
    <span class="small muted">
      {#if busy}Searching…{:else if error}<span class="err">{error}</span>
      {:else if result}{result.total} found{result.total > result.shown ? `, showing the newest ${result.shown}` : ''}{result.area ? ` in ${result.area}` : ''}{/if}
    </span>
  </div>

  {#each groups as [day, list] (day)}
    {@const sd = shortDay(day)}
    <section class="day">
      <h2>{sd.dow} {sd.date} <span class="muted">· {list.length}</span></h2>
      <ul class="results">
        {#each list as o (o.camera + o.id)}
          <li>
            <a class="result" href={openIn(o)} title="Open in the Player at {clock(o.enter, true)}">
              <img src={media.thumb(o.day, o.camera, o.id)} alt="{o.category} on {o.camera} at {clock(o.enter)}" loading="lazy" />
              <span class="meta">
                <span class="when mono"><span class="dot {o.category}"></span>{clock(o.enter, true)}</span>
                <span class="small muted">{o.camera} · {duration(o.t1 - o.t0)}</span>
              </span>
            </a>
            <CorrectMenu day={o.day} camera={o.camera} obj={o} onchange={(_, v) => fixed(o, v)} />
          </li>
        {/each}
      </ul>
    </section>
  {:else}
    {#if result && !busy}<div class="empty">Nothing matches. Try a longer date range or fewer filters.</div>{/if}
  {/each}
</div>

<style>
  .page { padding: 24px 32px 40px; display: flex; flex-direction: column; gap: 16px; }
  h1 { font-size: 22px; font-weight: 600; }
  .sub { font-size: 13px; }
  .form { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; align-items: start; }
  .form .wide { grid-column: span 3; }
  .form .time { grid-column: span 2; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field label, .lbl { font-size: 13px; color: var(--muted); }
  .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .row.tight { gap: 6px; }
  .row.tight input { flex: 1; min-width: 0; }
  .presets { margin-top: 2px; flex-wrap: nowrap; }
  .presets :global(.btn) { white-space: nowrap; padding: 0 10px; min-height: 30px; font-size: 12.5px; }
  .small { font-size: 13px; }
  .err { color: var(--bad); }
  h2 { font-size: 15px; font-weight: 600; margin: 4px 0 8px; }
  .results { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
  .results li { position: relative; }
  .result { display: flex; flex-direction: column; gap: 6px; padding: 8px; border-radius: 10px; border: 1px solid var(--line);
            background: var(--surface-2); color: inherit; text-decoration: none; }
  .result:hover { border-color: var(--accent); }
  .result img { width: 100%; height: 90px; object-fit: contain; background: var(--inset); border-radius: 6px; }
  .meta { display: flex; flex-direction: column; gap: 2px; }
  .when { display: flex; align-items: center; gap: 6px; font-size: 13px; }
</style>

<script>
  // Trends across days: totals, objects per day (stacked by type), when in the day things happen
  // (an hour × day heatmap), each camera's days, and each saved report area's days.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { clock, shortDay } from '../lib/format.js';

  let { params } = $props();

  const TYPES = [['person', 'People'], ['vehicle', 'Vehicles'], ['animal', 'Animals']];
  const RANGES = [[7, 'Last 7 days'], [14, 'Last 14 days'], [30, 'Last 30 days'], [0, 'Everything']];
  const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); };

  let range = $state(Number(params.range ?? 14));
  let cameras = $state([]);
  let chosen = $state([]);
  let types = $state(params.types ? params.types.split(',') : TYPES.map(([k]) => k));
  let data = $state(null);
  let busy = $state(false);
  let error = $state('');

  onMount(async () => {
    const s = await api.settings();
    cameras = s.cameras;
    chosen = params.cams ? params.cams.split(',') : [...s.cameras];
  });

  $effect(() => {
    const q = range ? { start: daysAgo(range), end: daysAgo(0), cameras: chosen.join(',') } : { cameras: chosen.join(',') };
    if (!chosen.length) return;
    history.replaceState(null, '', `#/trends?${new URLSearchParams({ range: String(range), cams: chosen.join(','), types: types.join(',') })}`);
    busy = true;
    error = '';
    api.trends(q).then((d) => (data = d)).catch((e) => (error = e.message)).finally(() => (busy = false));
  });

  const toggle = (list, k) => (list.includes(k) ? list.filter((x) => x !== k) : [...list, k]);
  const shown = $derived(TYPES.filter(([k]) => types.includes(k)));
  // days that have analysis on at least one chosen camera
  const days = $derived((data?.days ?? []).filter((d) => chosen.some((c) => data.data[c]?.[d])));

  function sumCounts(c, d) {
    const x = data.data[c]?.[d]?.counts;
    return x ? shown.reduce((s, [k]) => s + (x[k] ?? 0), 0) : 0;
  }
  const perDay = $derived(days.map((d) => {
    const by = Object.fromEntries(shown.map(([k]) => [k, chosen.reduce((s, c) => s + (data.data[c]?.[d]?.counts?.[k] ?? 0), 0)]));
    return { day: d, by, total: Object.values(by).reduce((a, b) => a + b, 0) };
  }));
  const heat = $derived(days.map((d) => Array.from({ length: 24 }, (_, h) =>
    chosen.reduce((s, c) => s + shown.reduce((t, [k]) => t + (data.data[c]?.[d]?.hourly_by?.[k]?.[h] ?? 0), 0), 0))));
  const heatMax = $derived(Math.max(1, ...heat.flat()));
  const total = $derived(perDay.reduce((s, x) => s + x.total, 0));
  const busiestDay = $derived(perDay.reduce((a, b) => (b.total > (a?.total ?? -1) ? b : a), null));
  const byHour = $derived(Array.from({ length: 24 }, (_, h) => heat.reduce((s, row) => s + row[h], 0)));
  const busiestHour = $derived(byHour.indexOf(Math.max(...byHour)));
  const areaRows = $derived((data?.areas ?? []).filter((a) => chosen.includes(a.camera)));

  function niceMax(m) {
    if (m <= 2) return 2;
    const p = 10 ** Math.floor(Math.log10(m));
    return [1, 2, 4, 6, 8, 10].map((f) => f * p).find((v) => v >= m);
  }
  const top = $derived(niceMax(Math.max(0, ...perDay.map((x) => x.total))));
  const dayLabel = (d) => { const s = shortDay(d); return `${s.dow} ${s.date}`; };
  // Sequential single hue for the heatmap (the app's blue): near zero recedes into the surface.
  const heatColor = (v) => (v ? `rgba(110, 160, 255, ${0.12 + 0.88 * (v / heatMax)})` : 'transparent');
</script>

<div class="page">
  <header>
    <div>
      <h1>Trends</h1>
      <div class="muted sub">How activity changes from day to day</div>
    </div>
    <div class="push row">
      <select bind:value={range} aria-label="Range">{#each RANGES as [v, l]}<option value={v}>{l}</option>{/each}</select>
      <div class="seg" role="group" aria-label="Cameras">
        {#each cameras as c}<button aria-pressed={chosen.includes(c)} onclick={() => { const t = toggle(chosen, c); if (t.length) chosen = t; }}>{c}</button>{/each}
      </div>
      <div class="seg" role="group" aria-label="Types">
        {#each TYPES as [k, l]}<button aria-pressed={types.includes(k)} onclick={() => { const t = toggle(types, k); if (t.length) types = t; }}><span class="dot {k}"></span> {l}</button>{/each}
      </div>
    </div>
  </header>

  {#if error}<div class="panel err">{error}</div>{/if}
  {#if busy && !data}<div class="empty">Loading…</div>{/if}

  {#if data && !days.length}
    <div class="empty">No analyzed days in this range yet.</div>
  {:else if data}
    <section class="tiles" aria-label="Summary">
      <div class="tile"><span class="big">{total}</span><span class="muted small">objects in {days.length} day{days.length === 1 ? '' : 's'}</span></div>
      <div class="tile"><span class="big">{days.length ? Math.round(total / days.length) : 0}</span><span class="muted small">per day on average</span></div>
      <div class="tile"><span class="big">{busiestDay ? dayLabel(busiestDay.day) : '–'}</span><span class="muted small">busiest day{busiestDay ? ` · ${busiestDay.total}` : ''}</span></div>
      <div class="tile"><span class="big">{total ? clock(busiestHour * 3600).replace(':00', '') : '–'}</span><span class="muted small">busiest hour of the day</span></div>
    </section>

    <section class="panel" aria-labelledby="pd-h">
      <div class="row"><h2 id="pd-h">Objects per day</h2>
        <span class="legend push">{#each shown as [k, l]}<span><span class="dot {k}"></span>{l}</span>{/each}</span></div>
      <div class="bars">
        <div class="yaxis mono dim"><span>{top}</span><span>{top / 2}</span><span>0</span></div>
        <div class="plot">
          <div class="grid" style:bottom="50%"></div><div class="grid" style:bottom="100%"></div>
          {#each perDay as x (x.day)}
            <a class="col" href={`#/player?day=${x.day}`} title="{dayLabel(x.day)}: {x.total} ({shown.map(([k, l]) => `${x.by[k]} ${l.toLowerCase()}`).join(', ')})">
              {#each shown as [k], i}
                {#if x.by[k]}<span class="seg-bar {k}" class:topmost={shown.slice(i + 1).every(([j]) => !x.by[j])} style:height="{(x.by[k] / top) * 100}%"></span>{/if}
              {/each}
            </a>
          {/each}
        </div>
        <span></span>
        <div class="xaxis mono dim">{#each perDay as x (x.day)}<span>{days.length <= 16 || perDay.indexOf(x) % Math.ceil(days.length / 14) === 0 ? shortDay(x.day).date : ''}</span>{/each}</div>
      </div>
    </section>

    <section class="panel" aria-labelledby="hm-h">
      <div class="row"><h2 id="hm-h">When things happen</h2>
        <span class="legend push small muted">fewer <span class="ramp"></span> more (busiest hour: {heatMax})</span></div>
      <div class="heat" style:--rows={days.length}>
        <span></span>
        {#each Array(24) as _, h}<span class="hx mono dim">{h % 3 === 0 ? clock(h * 3600).replace(':00', '').replace(' ', '').toLowerCase() : ''}</span>{/each}
        {#each days as d, r (d)}
          <span class="hy small">{dayLabel(d)}</span>
          {#each heat[r] as v, h}<span class="cell" style:background={heatColor(v)} title="{dayLabel(d)}, {clock(h * 3600)}: {v}"></span>{/each}
        {/each}
      </div>
    </section>

    <section class="panel" aria-labelledby="cam-h">
      <h2 id="cam-h">By camera</h2>
      <table>
        <thead><tr><th>Camera</th>{#each days as d (d)}<th class="num">{shortDay(d).date}</th>{/each}<th class="num">Total</th></tr></thead>
        <tbody>
          {#each chosen as c}
            <tr><td>{c}</td>{#each days as d (d)}<td class="num">{data.data[c]?.[d] ? sumCounts(c, d) : '–'}</td>{/each}
              <td class="num strong">{days.reduce((s, d) => s + sumCounts(c, d), 0)}</td></tr>
          {/each}
        </tbody>
      </table>
    </section>

    <section class="panel" aria-labelledby="ar-h">
      <h2 id="ar-h">Areas</h2>
      {#if areaRows.length}
        <table>
          <thead><tr><th>Area</th>{#each days as d (d)}<th class="num">{shortDay(d).date}</th>{/each}<th class="num">Total</th></tr></thead>
          <tbody>
            {#each areaRows as a}
              {@const n = (d) => (a.days[d] ? shown.reduce((s, [k]) => s + (a.days[d][k] ?? 0), 0) : null)}
              <tr><td>{a.camera} · {a.name}</td>{#each days as d (d)}<td class="num">{n(d) ?? '–'}</td>{/each}
                <td class="num strong">{days.reduce((s, d) => s + (n(d) ?? 0), 0)}</td></tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <p class="muted small">No saved areas yet: draw them in a PDF report or save them from Find in area in the Player.</p>
      {/if}
    </section>
  {/if}
</div>

<style>
  .page { padding: 24px 32px 40px; display: flex; flex-direction: column; gap: 16px; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 22px; font-weight: 600; }
  h2 { font-size: 15px; font-weight: 600; }
  .sub { font-size: 13px; }
  .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .push { margin-left: auto; }
  .small { font-size: 12.5px; }
  .err { color: var(--bad); }
  .panel { display: flex; flex-direction: column; gap: 12px; overflow-x: auto; }
  .legend { display: inline-flex; gap: 12px; font-size: 12.5px; color: var(--muted); align-items: center; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }

  .tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
  .tile { display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; border-radius: 12px; background: var(--surface); border: 1px solid var(--line); }
  .big { font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; }

  .bars { display: grid; grid-template-columns: auto 1fr; column-gap: 8px; row-gap: 4px; }
  .yaxis { display: flex; flex-direction: column; justify-content: space-between; align-items: flex-end; height: 180px; font-size: 11px; }
  .plot { position: relative; display: flex; align-items: flex-end; gap: 6px; height: 180px; border-bottom: 1px solid var(--muted); }
  .grid { position: absolute; left: 0; right: 0; border-top: 1px dashed rgba(255, 255, 255, 0.08); }
  .col { flex: 1; height: 100%; display: flex; flex-direction: column-reverse; gap: 2px; min-width: 6px; }
  .col:hover { background: rgba(142, 168, 255, 0.07); }
  .seg-bar { display: block; width: 100%; min-height: 2px; }
  .seg-bar.topmost { border-radius: 3px 3px 0 0; }
  .seg-bar.person { background: var(--person); } .seg-bar.vehicle { background: var(--vehicle); } .seg-bar.animal { background: var(--animal); }
  .xaxis { display: flex; gap: 6px; font-size: 11px; }
  .xaxis span { flex: 1; text-align: center; min-width: 6px; white-space: nowrap; }

  .heat { display: grid; grid-template-columns: 110px repeat(24, minmax(14px, 1fr)); gap: 2px; align-items: center; }
  .hx { font-size: 10.5px; }
  .hy { color: var(--muted); white-space: nowrap; }
  .cell { height: 18px; border-radius: 3px; background: transparent; outline: 1px solid rgba(255, 255, 255, 0.03); }
  .ramp { display: inline-block; width: 90px; height: 10px; border-radius: 5px; background: linear-gradient(90deg, rgba(110, 160, 255, 0.12), rgba(110, 160, 255, 1)); }

  table { border-collapse: collapse; font-size: 13px; width: max-content; min-width: 100%; }
  th, td { padding: 6px 10px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
  th { font-weight: 500; color: var(--muted); font-size: 12px; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .strong { font-weight: 600; }
</style>

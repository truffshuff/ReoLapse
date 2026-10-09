<script>
  import { onMount } from 'svelte';
  import Icon from '../components/Icon.svelte';
  import { api, media, onJobEvents } from '../lib/api.js';
  import { longDay, shortDay, STATUS, CAT_LABEL, STREAM_NAME, modelName } from '../lib/format.js';

  let days = $state([]);
  let index = $state(0);
  let error = $state('');
  let loaded = $state(false);

  const current = $derived(days[index]);
  let kept = $state({}); // day -> note: days the storage cleanup never deletes
  let note = $state('');
  let editingNote = $state(false);
  const isKept = $derived(current && current.day in kept);

  async function toggleKeep() {
    if (isKept) {
      kept = await api.releaseDay(current.day);
      editingNote = false;
    } else {
      kept = await api.keepDay(current.day, '');
      note = '';
      editingNote = true; // offer a note right away
    }
  }

  async function saveNote() {
    kept = await api.keepDay(current.day, note);
    editingNote = false;
  }

  async function load() {
    try {
      [days, kept] = await Promise.all([api.days(), api.settings().then((s) => s.kept ?? {})]);
      error = '';
    } catch (e) {
      error = e.message;
    }
    loaded = true;
  }

  onMount(() => {
    load();
    // Refresh when a job finishes (new outputs) and every 30 s while visible.
    const stop = onJobEvents({ job: (e) => ['done', 'failed'].includes(e.job.status) && load() });
    const timer = setInterval(load, 30000);
    return () => { stop(); clearInterval(timer); };
  });

  function summary(day) {
    const ready = day.cameras.filter((c) => c.status === 'rendered').length;
    return `${day.cameras.length} cameras · ${ready} ready`;
  }

  function bars(hourly) {
    const max = Math.max(1, ...hourly);
    return hourly.map((v) => Math.round((40 * v) / max));
  }

  function synopsis(cam) {
    return cam.outputs.find((o) => o.name.startsWith('synopsis_'));
  }
</script>

<div class="page">
  <header>
    <div class="nav-btns">
      <button class="btn icon-btn" aria-label="Previous day" disabled={index >= days.length - 1} onclick={() => index++}><Icon name="prev" size={16} /></button>
      <button class="btn icon-btn" aria-label="Next day" disabled={index <= 0} onclick={() => index--}><Icon name="next" size={16} /></button>
    </div>
    <div>
      <h1>{current ? longDay(current.day) : 'Today'}</h1>
      <div class="muted sub">{current ? summary(current) : ' '}</div>
    </div>
    {#if current}
      <div class="push keep">
        {#if editingNote}
          <input type="text" bind:value={note} maxlength="200" placeholder="Why keep it? (optional)" aria-label="Note for this day"
                 onkeydown={(e) => e.key === 'Enter' && saveNote()} />
          <button class="btn" onclick={saveNote}>Save note</button>
        {:else if isKept && kept[current.day]}
          <span class="small muted" title="Note">{kept[current.day]}</span>
        {/if}
        <button class="btn" class:on={isKept} aria-pressed={isKept} onclick={toggleKeep}
                title={isKept ? 'Kept: storage cleanup never deletes this day. Click to release it.' : 'Keep this day: storage cleanup will never delete its footage or results'}>
          {isKept ? '★ Kept' : '☆ Keep this day'}</button>
      </div>
      <a class="btn" href={`#/report?day=${current.day}`}>PDF report</a>
    {/if}
    <a class="btn primary" class:push={!current} href="#/jobs">Process a day</a>
  </header>

  {#if error}
    <div class="panel err">Can't reach the reolapse server: {error}</div>
  {:else if loaded && !days.length}
    <div class="empty">No days yet. Use <a href="#/jobs">Jobs</a> to download and process a day.</div>
  {/if}

  {#if current}
    <section class="cams" aria-label="Cameras">
      {#each current.cameras as cam (cam.camera)}
        {@const st = STATUS[cam.status]}
        {@const syn = synopsis(cam)}
        <article class="card cam">
          <div class="thumb">
            {#if cam.backgrounds}
              <img src={media.bg(current.day, cam.camera, 78)} alt="{cam.camera} camera view" loading="lazy" />
            {:else}
              <div class="placeholder dim">No preview yet</div>
            {/if}
            <span class="tag">{cam.camera}</span>
            <span class="badge {st.cls} state">{st.label}</span>
            {#if syn}
              <a class="play" href={`#/player?day=${current.day}&camera=${encodeURIComponent(cam.camera)}`} aria-label="Play {cam.camera} synopsis"><Icon name="play" size={22} /></a>
            {/if}
          </div>
          <div class="body">
            <div class="counts">
              {#each Object.entries(cam.counts) as [cat, n]}
                <span><span class="dot {cat}"></span>{n} {CAT_LABEL[cat] ?? cat}</span>
              {/each}
            </div>
            <div>
              <div class="spark" aria-label="Activity by hour">
                {#each bars(cam.hourly) as h}<div style:height="{Math.max(2, h)}px" class:none={!h}></div>{/each}
              </div>
              <div class="axis mono dim"><span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>12a</span></div>
            </div>
            <div class="muted small">
              {[STREAM_NAME[cam.footage?.stream], cam.analysis?.model && `${modelName(cam.analysis.model)} · ${cam.analysis.fps} fps`,
                `${cam.recordings} recording files`, `${cam.outputs.length} outputs`].filter(Boolean).join(' · ')}
            </div>
          </div>
        </article>
      {/each}
    </section>

    <section class="recent" aria-label="Recent days">
      <h2>Recent days</h2>
      <div class="days">
        {#each days.slice(0, 7) as d, i (d.day)}
          {@const sd = shortDay(d.day)}
          <button class:sel={i === index} onclick={() => (index = i)}>
            <span class="muted small">{sd.dow}</span>
            <span class="date">{sd.date}</span>
            <span class="small dim">{summary(d)}{d.day in kept ? ' · ★ kept' : ''}</span>
          </button>
        {/each}
      </div>
    </section>
  {/if}
</div>

<style>
  .page { padding: 28px 36px 40px; display: flex; flex-direction: column; gap: 28px; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .nav-btns { display: flex; gap: 8px; }
  .keep { display: flex; align-items: center; gap: 8px; }
  .keep input { width: 220px; }
  .keep .btn.on { border-color: var(--warn); color: var(--warn); }
  h1 { font-size: 26px; font-weight: 600; }
  .sub { font-size: 14px; }
  .push { margin-left: auto; }
  .err { color: var(--bad); }
  .cams { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 20px; }
  .cam { overflow: hidden; display: flex; flex-direction: column; }
  .thumb { position: relative; aspect-ratio: 16 / 7; background: var(--inset); }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .placeholder { height: 100%; display: flex; align-items: center; justify-content: center; font-size: 13px; }
  .tag { position: absolute; left: 12px; top: 12px; padding: 4px 10px; border-radius: 8px; background: rgba(14,16,20,0.82); font-size: 13px; font-weight: 500; }
  .state { position: absolute; right: 12px; top: 12px; }
  .play { position: absolute; left: 50%; top: 50%; width: 56px; height: 56px; margin: -28px 0 0 -28px; border-radius: 28px;
          background: rgba(14,16,20,0.78); display: flex; align-items: center; justify-content: center; color: var(--text); }
  .body { padding: 16px 18px 18px; display: flex; flex-direction: column; gap: 14px; }
  .counts { display: flex; gap: 16px; font-size: 14px; flex-wrap: wrap; }
  .counts > span { display: flex; align-items: center; gap: 6px; }
  .spark { display: flex; align-items: flex-end; gap: 2px; height: 40px; }
  .spark div { flex: 1; background: var(--accent); border-radius: 2px; }
  .spark div.none { background: var(--line-strong); }
  .axis { display: flex; justify-content: space-between; font-size: 11px; margin-top: 6px; }
  .small { font-size: 13px; }
  .recent h2 { font-size: 17px; font-weight: 600; margin-bottom: 14px; }
  .days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; }
  .days button { min-height: 72px; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface);
                 text-align: left; display: flex; flex-direction: column; gap: 4px; }
  .days button.sel { border-color: var(--accent); }
  .date { font-size: 16px; font-weight: 500; }
</style>

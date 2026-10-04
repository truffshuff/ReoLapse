<script>
  import { onMount, tick } from 'svelte';
  import { api, onJobEvents } from '../lib/api.js';
  import { yesterday, duration, CAT_LABEL } from '../lib/format.js';
  import Switch from '../components/Switch.svelte';
  import InfoTip from '../components/InfoTip.svelte';
  import { jobState } from '../lib/busy.svelte.js';

  const KINDS = [
    { id: 'run', label: 'Everything (download → find objects → render)' },
    { id: 'fetch', label: 'Download only' },
    { id: 'analyze', label: 'Find moving objects' },
    { id: 'render', label: 'Render videos & stills' },
  ];
  const TITLE = { fetch: 'Download', analyze: 'Find moving objects', render: 'Render', run: 'Everything' };
  const LANES = [
    { id: 'download', label: 'Download lane', kinds: ['fetch'] },
    { id: 'process', label: 'Processing lane', kinds: ['analyze', 'render', 'run'] },
  ];
  const STATUS_CLS = { queued: '', running: 'run', done: 'ok', failed: 'bad', cancelled: '' };

  let jobs = $state(new Map());
  let selected = $state(null);
  let log = $state([]);
  let logEl = $state();
  let now = $state(Date.now() / 1000);

  let kind = $state('run');
  let only = $state('');
  let day = $state(yesterday());
  let cameras = $state([]);
  let chosen = $state({});
  let error = $state('');

  const list = $derived([...jobs.values()]);
  const running = $derived(list.filter((j) => j.status === 'running'));
  const upNext = $derived(list.filter((j) => j.status === 'queued').sort((a, b) => a.order - b.order));
  const finished = $derived(list.filter((j) => !['queued', 'running'].includes(j.status)).sort((a, b) => (b.finished ?? b.id) - (a.finished ?? a.id)));
  const current = $derived(selected != null ? jobs.get(selected) : null);

  async function select(id) {
    selected = id;
    log = (await api.job(id)).log;
    await tick();
    logEl?.scrollTo(0, logEl.scrollHeight);
  }

  onMount(() => {
    api.settings().then((s) => {
      cameras = s.cameras;
      chosen = Object.fromEntries(s.cameras.map((c) => [c, true]));
    });
    const stop = onJobEvents({
      snapshot: (snap) => {
        jobs = new Map(snap.map((j) => [j.id, j]));
        if (selected == null && snap.length) select(Math.max(...snap.map((j) => j.id)));
      },
      job: async (e) => {
        jobs.set(e.job.id, e.job);
        jobs = new Map(jobs);
        if (selected == null) select(e.job.id);
        if (e.line && e.job.id === selected) {
          const atBottom = logEl && logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 8;
          log = [...log.slice(-399), e.line];
          if (atBottom) { await tick(); logEl?.scrollTo(0, logEl.scrollHeight); }
        }
      },
      removed: (ids) => {
        ids.forEach((id) => jobs.delete(id));
        jobs = new Map(jobs);
        if (ids.includes(selected)) { selected = null; log = []; }
      },
    });
    const timer = setInterval(() => (now = Date.now() / 1000), 1000);
    return () => { stop(); clearInterval(timer); };
  });

  async function submit(e) {
    e.preventDefault();
    error = '';
    const cams = cameras.filter((c) => chosen[c]);
    if (!cams.length) { error = 'Pick at least one camera.'; return; }
    try {
      const job = await api.submit(kind, day, cams, kind === 'render' && only ? { only: [only] } : {});
      select(job.id);
      if (kind === 'run') error = '';
    } catch (err) {
      error = err.message;
    }
  }

  function elapsed(j) {
    if (!j.started) return '';
    return duration((j.finished ?? now) - j.started);
  }

  function progressText(p) {
    if (!p || !p.stage) return '';
    return [p.stage, p.camera, p.detail].filter(Boolean).join(' · ');
  }
</script>

<div class="page">
  <header>
    <div>
      <h1>Jobs</h1>
      <div class="muted sub">Two lanes run side by side: downloading, and processing (finding objects, rendering). A day's steps always run in order.</div>
    </div>
    <div class="push row">
      <InfoTip text="Pause stops the running jobs right away and starts nothing new until you resume. Each job continues from its last checkpoint: downloads keep every finished file and object finding keeps everything found so far (it saves every two minutes of footage). The queue also pauses itself when the network share can't be reached, for example when the laptop leaves home, and resumes when it's back." />
      {#if jobState.paused}
        <button class="btn primary" onclick={() => api.resumeQueue()}>Resume queue</button>
      {:else}
        <button class="btn" onclick={() => api.pauseQueue()}>Pause queue</button>
      {/if}
    </div>
  </header>

  {#if jobState.paused}
    <div class="banner" role="status">
      <strong>Queue paused</strong>: {jobState.reason}.
      {#if jobState.auto}It will resume by itself when the share is reachable again.{:else}Nothing runs until you resume.{/if}
    </div>
  {/if}

  <div class="grid">
    <section aria-label="Jobs" class="jobs">
      {#snippet card(j, actions)}
        <div class="job" class:sel={j.id === selected}>
          <button class="pick" onclick={() => select(j.id)} aria-label="Show log for job {j.id}">
            <div class="row">
              <span class="title">{TITLE[j.kind] ?? j.kind}</span>
              {#if j.options?.only?.length}<span class="small dim">{j.options.only.map((k) => CAT_LABEL[k] ?? k).join(', ')} only</span>{/if}
              {#if j.options?.no_video}<span class="small dim">pictures only</span>{/if}
              <span class="mono muted">{j.day}</span>
              {#if j.group}<span class="small dim">part of job {j.group}</span>{/if}
              <span class="badge {STATUS_CLS[j.status]} push">{j.status}</span>
            </div>
            <div class="row small muted">
              <span>{j.cameras.join(', ')}</span>
              <span class="push mono">{elapsed(j)}</span>
            </div>
            {#if j.status === 'running'}
              <div class="bar"><div style:width="{Math.round((j.progress?.frac ?? 0) * 100)}%"></div></div>
              <div class="small muted">{progressText(j.progress) || 'working…'}</div>
              {#if j.status_line}<div class="small mono status">{j.status_line}</div>{/if}
            {:else if j.status === 'queued' && j.waiting}
              <div class="small waiting">{j.waiting}</div>
            {/if}
          </button>
          {#if actions}<div class="actions">{@render actions(j)}</div>{/if}
        </div>
      {/snippet}

      {#snippet queuedActions(j)}
        <button class="btn icon-btn mini" aria-label="Move job {j.id} up" disabled={upNext[0]?.id === j.id} onclick={() => api.move(j.id, -1)}>↑</button>
        <button class="btn icon-btn mini" aria-label="Move job {j.id} down" disabled={upNext.at(-1)?.id === j.id} onclick={() => api.move(j.id, 1)}>↓</button>
        <button class="btn quiet mini" onclick={() => api.cancel(j.id)}>Cancel</button>
      {/snippet}

      {#snippet finishedActions(j)}
        {#if j.status === 'failed' || j.status === 'cancelled'}
          <button class="btn quiet mini" onclick={() => api.retry(j.id)}>Retry</button>
        {/if}
        <button class="btn quiet mini" aria-label="Remove job {j.id} from the list" onclick={() => api.remove(j.id)}>Remove</button>
      {/snippet}

      {#if !list.length}
        <div class="empty panel">No jobs yet. Start one on the right.</div>
      {/if}

      {#if list.length}
        <h2 class="sec">Running</h2>
        {#each LANES as lane}
          {@const r = running.find((j) => lane.kinds.includes(j.kind))}
          {#if r}{@render card(r, null)}{:else}<div class="idle small dim">{lane.label}: idle</div>{/if}
        {/each}
      {/if}

      {#if upNext.length}
        <h2 class="sec">Up next</h2>
        {#each upNext as j (j.id)}{@render card(j, queuedActions)}{/each}
      {/if}

      {#if finished.length}
        <div class="sec-row"><h2 class="sec">Finished</h2>
          <button class="btn quiet mini" onclick={() => confirm(`Remove all ${finished.length} finished jobs and their logs from the list? Downloads, results and videos are not affected.`) && api.clearFinished()}>Clear finished</button></div>
        {#each finished.slice(0, 20) as j (j.id)}{@render card(j, finishedActions)}{/each}
      {/if}
    </section>

    <aside class="side">
      <form class="panel form" onsubmit={submit}>
        <h2>Process a day</h2>
        <div class="field"><label for="day">Day</label><input id="day" type="date" bind:value={day} required /></div>
        <div class="field">
          <div class="lab"><label for="kind">What to do</label>
            <InfoTip text="Everything queues three jobs for the day: download from the NVR, find moving objects, and render the videos and stills. They run in that order, but a download can run alongside another day's processing. Pick a single step to redo just that part, for example re-render after changing styles in Settings." /></div>
          <select id="kind" bind:value={kind}>{#each KINDS as k}<option value={k.id}>{k.label}</option>{/each}</select>
        </div>
        {#if kind === 'render'}
          <div class="field">
            <div class="lab"><span class="lbl">Only</span>
              <InfoTip text="Render a video of just one kind of object for this day, alongside the full one; it shows up in the Player under Show. The day's stills are left as they are." /></div>
            <div class="seg" role="group" aria-label="Only">
              {#each [['', 'Everything'], ['person', 'People'], ['vehicle', 'Vehicles'], ['animal', 'Animals']] as [k, label]}
                <button type="button" aria-pressed={only === k} onclick={() => (only = k)}>{label}</button>
              {/each}
            </div>
          </div>
        {/if}
        <fieldset>
          <legend class="muted">Cameras</legend>
          {#each cameras as c}
            <Switch label={c} bind:checked={chosen[c]} />
          {/each}
        </fieldset>
        {#if error}<div class="err small">{error}</div>{/if}
        <button class="btn primary" type="submit">Queue job</button>
      </form>

      {#if current}
        <section class="panel logbox" aria-label="Log">
          <div class="row">
            <h2>Log · job {current.id}</h2>
            {#if current.status === 'queued' || current.status === 'running'}
              <button class="btn quiet push" onclick={() => api.cancel(current.id)}>Cancel</button>
            {/if}
          </div>
          <pre bind:this={logEl} class="mono">{log.join('\n') || 'No output yet.'}</pre>
          {#if current.status === 'running' && current.status_line}
            <div class="live mono" aria-live="polite"><span class="pulse" aria-hidden="true"></span>{current.status_line}</div>
          {/if}
          <a class="btn quiet dl" href={`/api/jobs/${current.id}/log`} download>Download full log</a>
        </section>
      {/if}
    </aside>
  </div>
</div>

<style>
  .page { padding: 24px 32px 32px; display: flex; flex-direction: column; gap: 22px; }
  h1 { font-size: 22px; font-weight: 600; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .banner { padding: 12px 16px; border-radius: 10px; background: #2E2A1F; border: 1px solid #4A3F28; color: #F3DDB8; font-size: 14px; }
  h2 { font-size: 15px; font-weight: 600; }
  .sub { font-size: 13px; }
  .grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 20px; align-items: start; }
  .jobs { display: flex; flex-direction: column; gap: 10px; }
  .job { display: flex; align-items: stretch; border-radius: 12px; background: var(--surface); border: 1px solid var(--line); }
  .job.sel { border-color: var(--accent); }
  .pick { flex: 1; min-width: 0; text-align: left; padding: 14px 16px; border: 0; background: transparent;
          display: flex; flex-direction: column; gap: 8px; border-radius: 12px; }
  .actions { display: flex; align-items: center; gap: 6px; padding: 0 12px; }
  .mini { min-height: 36px; padding: 0 10px; font-size: 13px; }
  .icon-btn.mini { width: 36px; padding: 0; }
  .sec { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 6px; }
  .sec-row { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; }
  .sec-row .sec { margin-top: 0; }
  .idle { padding: 12px 16px; border-radius: 12px; border: 1px dashed var(--line); }
  .waiting { color: var(--warn); }
  .dim { color: var(--dim); }
  .row { display: flex; align-items: center; gap: 12px; }
  .title { font-weight: 500; }
  .push { margin-left: auto; }
  .small { font-size: 13px; }
  .status { font-size: 12px; color: var(--accent-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .side { display: flex; flex-direction: column; gap: 16px; }
  .form { display: flex; flex-direction: column; gap: 14px; }
  fieldset { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
  legend { font-size: 13px; margin-bottom: 4px; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field label, .lbl { font-size: 13px; color: var(--muted); }
  .lab { display: flex; align-items: center; gap: 2px; }
  .err { color: var(--bad); }
  .logbox { display: flex; flex-direction: column; gap: 10px; }
  .live { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 8px; background: var(--accent-soft);
          color: var(--accent-text); font-size: 12px; line-height: 1.4; }
  .pulse { flex: none; width: 8px; height: 8px; border-radius: 4px; background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.3; } }
  .dl { align-self: flex-start; }
  pre { margin: 0; max-height: 420px; overflow: auto; padding: 12px 14px; border-radius: 10px; background: var(--inset);
        border: 1px solid var(--line); font-size: 12px; line-height: 1.6; color: #B9C0CB; white-space: pre-wrap; }
</style>

<script>
  import { onMount, tick } from 'svelte';
  import { api, onJobEvents } from '../lib/api.js';
  import { yesterday, duration } from '../lib/format.js';
  import Switch from '../components/Switch.svelte';
  import InfoTip from '../components/InfoTip.svelte';

  const KINDS = [
    { id: 'run', label: 'Everything (download → analyze → render)' },
    { id: 'fetch', label: 'Download only' },
    { id: 'analyze', label: 'Find moving objects' },
    { id: 'render', label: 'Render videos & stills' },
  ];
  const STATUS_CLS = { queued: '', running: 'run', done: 'ok', failed: 'bad', cancelled: '' };

  let jobs = $state(new Map());
  let selected = $state(null);
  let log = $state([]);
  let logEl = $state();
  let now = $state(Date.now() / 1000);

  let kind = $state('run');
  let day = $state(yesterday());
  let cameras = $state([]);
  let chosen = $state({});
  let error = $state('');

  const list = $derived([...jobs.values()].sort((a, b) => b.id - a.id));
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
      const job = await api.submit(kind, day, cams);
      select(job.id);
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
      <div class="muted sub">One job runs at a time; analysis uses the GPU.</div>
    </div>
  </header>

  <div class="grid">
    <section aria-label="Job list" class="jobs">
      {#if !list.length}
        <div class="empty panel">No jobs yet. Start one on the right.</div>
      {/if}
      {#each list as j (j.id)}
        <button class="job" class:sel={j.id === selected} onclick={() => select(j.id)}>
          <div class="row">
            <span class="title">{KINDS.find((k) => k.id === j.kind)?.label.split(' (')[0] ?? j.kind}</span>
            <span class="mono muted">{j.day}</span>
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
          {/if}
        </button>
      {/each}
    </section>

    <aside class="side">
      <form class="panel form" onsubmit={submit}>
        <h2>Process a day</h2>
        <div class="field"><label for="day">Day</label><input id="day" type="date" bind:value={day} required /></div>
        <div class="field">
          <div class="lab"><label for="kind">What to do</label>
            <InfoTip text="Everything downloads the day from the NVR, finds moving objects, then renders the videos and stills. Run a single step to redo just that part, for example re-render after changing styles in Settings." /></div>
          <select id="kind" bind:value={kind}>{#each KINDS as k}<option value={k.id}>{k.label}</option>{/each}</select>
        </div>
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
  h2 { font-size: 15px; font-weight: 600; }
  .sub { font-size: 13px; }
  .grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 20px; align-items: start; }
  .jobs { display: flex; flex-direction: column; gap: 10px; }
  .job { text-align: left; padding: 14px 16px; border-radius: 12px; background: var(--surface); border: 1px solid var(--line);
         display: flex; flex-direction: column; gap: 8px; }
  .job.sel { border-color: var(--accent); }
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
  .field label { font-size: 13px; color: var(--muted); }
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

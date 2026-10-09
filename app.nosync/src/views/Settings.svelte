<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import Switch from '../components/Switch.svelte';
  import InfoTip from '../components/InfoTip.svelte';

  const LOCAL_WORK = '~/Library/Caches/reolapse';
  const MOVING_LENS = /track|ptz/i; // TrackMix / TrackFlex lenses pan and zoom: no stable background

  const STYLES = [
    { id: 'cutout', label: 'Cutout', hint: 'Objects drawn solid over the scene' },
    { id: 'ghost', label: 'Ghost', hint: 'Semi-transparent; easier to read when it gets busy' },
    { id: 'outline', label: 'Outline', hint: 'Solid, with a colored edge around each object' },
  ];

  const PER_TYPE = [
    { id: 'person', label: 'People', hint: 'Just the people: no traffic in the way' },
    { id: 'vehicle', label: 'Vehicles', hint: 'Just the cars, trucks and bikes' },
    { id: 'animal', label: 'Animals', hint: 'Just the pets and wildlife' },
  ];

  const SIZES = [
    { value: 960, label: '960 · fast', about: 'Fine for nearby objects; may miss people far down the street.' },
    { value: 1280, label: '1280 · recommended', about: 'Good balance for the 1920-wide panoramas (~40 frames/s on this Mac).' },
    { value: 1920, label: '1920 · full detail', about: 'Best for small or distant objects, at about half the speed (~20 frames/s).' },
  ];

  const HELP = {
    fps: 'How many frames per second are checked for moving objects. 5 fps processes about twice as fast; 10 fps gives smoother motion in the synopsis and catches fast cars in more frames.',
    types: 'Whole day scans all 24 hours, running the detector only when pixels actually change; it catches everything, including passing cars. NVR events only scans just the stretches where the NVR reported a person, animal or motion: much faster, but it misses vehicles that never overlapped one.',
    model: 'The neural network that finds and outlines people, vehicles and animals. Larger models are more accurate but slower. A model that isn\'t downloaded yet downloads automatically the first time it\'s used.',
    imgsz: 'The image width the detector works at; each frame is scaled to this size first. Larger finds small or distant objects more reliably but processes more slowly.',
    styles: 'Which synopsis videos are rendered for each day. You can switch between them in the Player.',
    color: 'Type colors the time tags orange for people, blue for vehicles and green for animals. Time of day colors them by when the object was there, from blue at night through yellow at midday to teal in the evening.',
    drift: 'To reduce overlap, an object may be shown up to this many minutes away from its real time. 0 keeps every object at its exact time. The tag on each object always shows its real time.',
    speed: 'How fast objects move compared with real life. 2× keeps walking looking natural while fitting more into the video.',
    clock: 'Adaptive gives busy times of day more of the video and quiet ones (the night) less, so where things happen the clock runs closer to the objects\' own pace: an object\'s time tag and the clock stay closer together, and empty hours pass quickly. Off, the clock runs at one rate all day, so every hour gets the same share of the video. Applies to videos rendered after the change.',
    density: 'Sets the automatic video length: roughly how many objects are on screen at once. Higher makes a shorter, busier video.',
    raw: 'Downloaded recordings are the biggest files (about 5–21 GB per camera per day). Days older than this would be deleted; the finished videos and stills are kept separately.',
    results: 'How long finished videos, stills and the object data behind them are kept.',
    nightly: 'At this time each night, Reolapse queues yesterday for every included camera: the whole pipeline for a day with nothing yet, just a render for a day already analyzed, nothing for a day that\'s done. It also catches up on the few days before that, and if the Mac was off or asleep at that time it runs the next time Reolapse is open. Turn it on in one Mac only.',
    cleanup: 'Deletes what the limits above allow: downloaded footage older than the raw limit once that day has been rendered (footage that was never processed is kept), footage of the other stream after a day was downloaded again (for example Fluent files after a Clear download), leftovers of interrupted downloads, and everything for days older than the results limit. Days with a queued or running job are never touched.',
    cameras: 'Only fixed cameras work for synopsis: the background has to stay still. Tracking (pan/zoom) lenses are flagged.',
    stream: 'Fluent is the camera\'s low-resolution sub stream: small and quick, about 100x real time to process (Garage 1920×576, ~5 GB a day). Clear is the full-resolution main stream: much sharper cutouts, but far bigger and slower: Garage 5120×1552 is ~36 GB and ~45 min of downloading a day, Porch Left 4K is ~89 GB and ~1.7 h, and a whole-day scan takes about 3 hours per camera even on the hardware decoder. Clear frames are scaled to 2560 px wide for processing. A change applies the next time a day is downloaded; that day is then re-analyzed in the new stream.',
    perType: 'Each switch adds one more video per camera and day, in the first style above, containing only that kind of object. With fewer objects they are shorter, less crowded and quicker to render than the full video. The Player switches between them with Show.',
    work: 'Where backgrounds and object data are written and videos are rendered. These are many small, random reads and writes, so a folder on this Mac is much faster than the network share. Finished videos and stills are still copied to the archive, and days processed before you change this keep working from the share. About 1 GB per camera per day accumulates here.',
    archive: 'Where downloaded recordings, the NVR event lists and the finished videos and stills are kept: usually a folder on your NAS share, mounted on this Mac. Leave empty for the default. When it can\'t be reached, the job queue pauses itself.',
    updates: 'Reolapse checks its GitHub releases for a newer version. The repository is private, so it needs a GitHub token that can read it; the token is stored in the macOS Keychain. Because the app isn\'t signed by Apple it can\'t replace itself: the new version is downloaded to your Downloads folder and opened, and you drag Reolapse into Applications (replace the old one). Your settings, login and processed days are kept.',
    nvr: 'The address and login of your Reolink NVR. Use a dedicated NVR user (viewing and playback is enough) whose password has no & # % or spaces; the NVR\'s web API breaks on those. The password is stored in the macOS Keychain, not in a file.',
  };

  let s = $state(null);
  let saved = $state(null);
  let health = $state(null);
  let nvr = $state(null);
  let models = $state([]);
  let test = $state(null);
  let testing = $state(false);
  let message = $state('');
  let login = $state({ host: '', user: '', port: 80, password: '' });
  let loginSaved = $state('');
  let loginMsg = $state('');
  const loginDirty = $derived(JSON.stringify({ ...login, password: '' }) !== loginSaved || login.password !== '');

  function setLoginFrom(n) {
    login = { host: n.host ?? '', user: n.user ?? '', port: n.port ?? 80, password: '' };
    loginSaved = JSON.stringify({ ...login });
  }

  async function saveLogin() {
    loginMsg = '';
    try {
      nvr = await api.saveNvr($state.snapshot(login));
      setLoginFrom(nvr);
      // keep the form's copy in step, so it isn't "unsaved" and can't hold an older login
      const n = { host: nvr.host ?? '', user: nvr.user ?? '', port: nvr.port ?? 80 };
      s.nvr = { ...n };
      if (saved) saved.nvr = { ...n };
      loginMsg = 'Saved. The password is kept in the system keychain.';
    } catch (e) {
      loginMsg = 'Not saved: ' + e.message;
    }
  }

  let upd = $state(null);
  let night = $state(null);
  let store = $state(null);
  let storeBusy = $state('');
  const gb = (b) => (b == null ? '–' : b >= 1e12 ? `${(b / 1e12).toFixed(2)} TB` : `${(b / 1e9).toFixed(1)} GB`);

  async function loadStorage(refresh = false) {
    storeBusy = 'Checking…';
    try { store = await api.storage(refresh); } catch (e) { storeBusy = `Couldn't check: ${e.message}`; return; }
    storeBusy = '';
  }

  async function cleanNow() {
    if (!confirm(`Delete ${gb(store?.would_free)} now? This can't be undone.`)) return;
    storeBusy = 'Cleaning up…';
    try {
      const r = await api.cleanup();
      storeBusy = r.busy ? 'A cleanup is already running.' : `Freed ${gb(r.freed)}.`;
      store = await api.storage(true);
    } catch (e) {
      storeBusy = `Cleanup failed: ${e.message}`;
    }
  }

  async function runNightNow() {
    night = await api.nightlyRun();
  }
  let token = $state('');
  let updMsg = $state('');
  let checking = $state(false);

  async function checkUpdates() {
    checking = true;
    updMsg = '';
    upd = await api.update(true, true).catch((e) => ({ ...upd, error: e.message }));
    checking = false;
  }

  async function saveToken(value) {
    updMsg = '';
    try {
      upd = await api.saveUpdateToken(value);
      token = '';
      updMsg = value ? 'Token saved in the system keychain.' : 'Token removed.';
    } catch (e) {
      updMsg = 'Not saved: ' + e.message;
    }
  }

  async function downloadUpdate() {
    try {
      upd.download = await api.downloadUpdate();
      while (upd.download.status === 'downloading') {
        await new Promise((r) => setTimeout(r, 1000));
        upd = await api.update();
      }
    } catch (e) {
      updMsg = 'Download failed: ' + e.message;
    }
  }

  const mb = (n) => (n / 1e6).toFixed(0) + ' MB';

  const dirty = $derived(s && saved && JSON.stringify(s) !== JSON.stringify(saved));
  const model = $derived(models.find((m) => m.file === s?.analysis.model));
  const size = $derived(SIZES.find((x) => x.value === Number(s?.analysis.imgsz)));

  onMount(async () => {
    [s, health, nvr, models, upd] = await Promise.all([api.settings(), api.health(), api.nvr(), api.models(), api.update()]);
    api.nightly(true).then((n) => (night = n)).catch(() => {});
    loadStorage();
    setLoginFrom(nvr);
    saved = structuredClone($state.snapshot(s));
  });

  async function save() {
    message = '';
    try {
      // the login is saved by Save login, and report areas by the report page
      const { nvr: _login, areas: _areas, ...patch } = $state.snapshot(s);
      const out = await api.saveSettings(patch);
      s = out;
      saved = structuredClone(out);
      health = await api.health();
      message = 'Saved.';
    } catch (e) {
      message = 'Not saved: ' + e.message;
    }
  }

  async function runTest() {
    testing = true;
    test = await api.testNvr().catch((e) => ({ ok: false, error: e.message }));
    testing = false;
  }

  function setCamera(name, on) {
    s.cameras = on ? [...s.cameras.filter((c) => c !== name), name] : s.cameras.filter((c) => c !== name);
  }

  const streamOf = (name) => s.streams[name] ?? 'sub';

  function setStream(name, stream) {
    const next = { ...s.streams };
    if (stream === 'sub') delete next[name]; else next[name] = stream;
    s.streams = next;
  }

  function setPerType(kind, on) {
    const want = new Set(s.render.per_type);
    on ? want.add(kind) : want.delete(kind);
    s.render.per_type = PER_TYPE.map((x) => x.id).filter((x) => want.has(x));
  }

  function setStyle(style, on) {
    const want = new Set(s.render.styles);
    on ? want.add(style) : want.delete(style);
    s.render.styles = STYLES.map((x) => x.id).filter((x) => want.has(x));
  }

  // Cameras to offer: whatever the NVR reports (after a test), plus any already configured.
  const cameraRows = $derived.by(() => {
    if (!s) return [];
    const fromNvr = test?.ok ? test.channels : [];
    const names = [...new Set([...fromNvr.map((c) => c.name), ...s.cameras])];
    return names.map((name) => {
      const ch = fromNvr.find((c) => c.name === name);
      const moving = ch ? MOVING_LENS.test(ch.model) : false;
      const hint = [ch?.model, moving ? 'lens moves (tracking): synopsis not supported' : ''].filter(Boolean).join(' · ');
      return { name, hint, moving, on: s.cameras.includes(name) };
    });
  });
</script>

<div class="page">
  <header>
    <h1>Settings</h1>
    <div class="push row">
      {#if message}<span class="muted small">{message}</span>{/if}
      <button class="btn primary" disabled={!dirty} onclick={save}>Save changes</button>
    </div>
  </header>

  {#if s}
    <section class="panel" aria-labelledby="nvr-h">
      <div class="row">
        <h2 id="nvr-h">NVR connection</h2><InfoTip text={HELP.nvr} />
        {#if test?.ok}<span class="ok small">● Connected · {test.model}</span>{:else if test}<span class="bad small">● {test.error}</span>{/if}
      </div>
      <div class="cols3">
        <div class="field"><label for="nvr-host">Address</label><input id="nvr-host" type="text" bind:value={login.host} placeholder="e.g. 192.168.1.20" autocomplete="off" /></div>
        <div class="field"><label for="nvr-user">Username</label><input id="nvr-user" type="text" bind:value={login.user} autocomplete="off" /></div>
        <div class="field"><label for="nvr-pass">Password</label>
          <input id="nvr-pass" type="password" bind:value={login.password} autocomplete="new-password"
                 placeholder={nvr?.password_set ? 'saved (type to change)' : 'not set'} /></div>
      </div>
      <div class="row">
        <button class="btn" onclick={saveLogin} disabled={!loginDirty}>Save login</button>
        <button class="btn" onclick={runTest} disabled={testing}>{testing ? 'Testing…' : 'Test connection'}</button>
        {#if loginMsg}<span class="small muted">{loginMsg}</span>{/if}
      </div>
      {#if nvr?.source === '.env'}
        <p class="desc">Right now the login comes from the project's .env file, which takes priority over what's saved here.</p>
      {/if}
    </section>

    <section class="panel" aria-labelledby="cam-h">
      <div class="row"><h2 id="cam-h">Cameras</h2><InfoTip text={HELP.cameras} />
        {#if !test?.ok}<span class="dim small">Test the connection to list every NVR camera.</span>{/if}</div>
      <div class="cam-head small dim"><span></span><span class="lab">Stream<InfoTip text={HELP.stream} /></span><span>Include</span></div>
      {#each cameraRows as c (c.name)}
        <div class="cam-row">
          <div class="cam-text"><span class="name">{c.name}</span>{#if c.hint}<span class="small" class:warn={c.moving}>{c.hint}</span>{/if}</div>
          <div class="seg" role="group" aria-label="{c.name} stream">
            <button type="button" aria-pressed={streamOf(c.name) === 'sub'} onclick={() => setStream(c.name, 'sub')}>Fluent</button>
            <button type="button" aria-pressed={streamOf(c.name) === 'main'} onclick={() => setStream(c.name, 'main')}>Clear</button>
          </div>
          <Switch compact label="Include {c.name}" checked={c.on} onchange={(on) => setCamera(c.name, on)} />
        </div>
      {/each}
    </section>

    <div class="cols2">
      <section class="panel" aria-labelledby="an-h">
        <h2 id="an-h">Analysis</h2>
        <div class="field">
          <div class="lab"><label for="fps">Frame rate</label><InfoTip text={HELP.fps} /></div>
          <select id="fps" bind:value={s.analysis.fps}><option value={5}>5 fps · faster</option><option value={10}>10 fps · smoother</option></select>
        </div>
        <div class="field">
          <div class="lab"><label for="types">Where to look</label><InfoTip text={HELP.types} /></div>
          <select id="types" bind:value={s.analysis.types}><option value="all">Whole day</option><option value="person,animal,motion">NVR events only</option></select>
        </div>
        <div class="field">
          <div class="lab"><label for="model">Detection model</label><InfoTip text={HELP.model} /></div>
          <select id="model" bind:value={s.analysis.model}>
            {#each models as m}<option value={m.file}>{m.name}{m.downloaded ? '' : ' · will download'}</option>{/each}
          </select>
          {#if model}<p class="desc"><strong>{model.speed}</strong>{model.speed ? ' · ' : ''}{model.about}</p>{/if}
        </div>
        <div class="field">
          <div class="lab"><label for="imgsz">Detector size</label><InfoTip text={HELP.imgsz} /></div>
          <select id="imgsz" bind:value={s.analysis.imgsz}>
            {#each SIZES as z}<option value={z.value}>{z.label}</option>{/each}
            {#if !size}<option value={s.analysis.imgsz}>{s.analysis.imgsz} · custom</option>{/if}
          </select>
          {#if size}<p class="desc">{size.about}</p>{/if}
        </div>
      </section>

      <section class="panel" aria-labelledby="re-h">
        <div class="row"><h2 id="re-h">Rendering defaults</h2><InfoTip text={HELP.styles} /></div>
        <div class="switches">
          {#each STYLES as st}
            <Switch label={st.label} hint={st.hint} checked={s.render.styles.includes(st.id)} onchange={(on) => setStyle(st.id, on)} />
          {/each}
        </div>
        <div class="row"><span class="sub-h">Also make videos of only</span><InfoTip text={HELP.perType} /></div>
        <div class="switches">
          {#each PER_TYPE as pt}
            <Switch label={pt.label} hint={pt.hint} checked={s.render.per_type.includes(pt.id)} onchange={(on) => setPerType(pt.id, on)} />
          {/each}
        </div>
        <div class="field">
          <div class="lab"><label for="color">Color tags by</label><InfoTip text={HELP.color} /></div>
          <select id="color" bind:value={s.render.color}><option value="category">Type</option><option value="time">Time of day</option></select>
        </div>
        <div class="field">
          <div class="lab"><label for="drift">Allowed time shift</label><InfoTip text={HELP.drift} /><span class="val mono">{s.render.drift === 0 ? 'exact' : '±' + s.render.drift + ' min'}</span></div>
          <input id="drift" type="range" min="0" max="30" step="1" bind:value={s.render.drift} />
        </div>
        <div class="field">
          <div class="lab"><label for="speed">Object speed</label><InfoTip text={HELP.speed} /><span class="val mono">{s.render.speed}×</span></div>
          <input id="speed" type="range" min="1" max="8" step="0.5" bind:value={s.render.speed} />
        </div>
        <div class="field">
          <div class="lab"><label for="density">Objects on screen</label><InfoTip text={HELP.density} /><span class="val mono">~{s.render.density}</span></div>
          <input id="density" type="range" min="2" max="15" step="1" bind:value={s.render.density} />
        </div>
        <div class="field">
          <div class="lab"><span>Clock</span><InfoTip text={HELP.clock} /></div>
          <Switch label="Adaptive: busy times get more of the video" checked={s.render.clock === 'adaptive'}
                  onchange={(on) => (s.render.clock = on ? 'adaptive' : 'even')} />
          <Switch label="Also make the first style with the other clock, to switch in the Player" bind:checked={s.render.second_clock} />
        </div>
      </section>

      <section class="panel" aria-labelledby="st-h">
        <h2 id="st-h">Storage</h2>
        <div class="field">
          <div class="lab"><label for="data-root">Archive folder (raw footage, finished videos)</label><InfoTip text={HELP.archive} /></div>
          <input id="data-root" type="text" bind:value={s.storage.data_root} placeholder={health?.data_root ?? ''} />
          <p class="desc">In use: <span class="mono">{health?.data_root ?? ''}</span>{health && !health.data_root_available ? ' (not reachable right now)' : ''}</p>
        </div>
        <div class="field">
          <div class="lab"><label for="work-root">Working folder</label><InfoTip text={HELP.work} /></div>
          <div class="inline">
            <input id="work-root" type="text" bind:value={s.storage.work_root} placeholder="Same as archive (slower)" />
            <button class="btn" type="button" onclick={() => (s.storage.work_root = LOCAL_WORK)}>Use this Mac's drive</button>
          </div>
          <p class="desc">In use: <span class="mono">{health?.work_root ?? ''}</span>{health?.work_root === health?.data_root ? ' (the archive share)' : ''}</p>
        </div>
        <div class="cols2 inner">
          <div class="field">
            <div class="lab"><label for="raw-days">Keep raw footage (days)</label><InfoTip text={HELP.raw} /></div>
            <input id="raw-days" type="number" min="1" bind:value={s.retention.raw_days} />
          </div>
          <div class="field">
            <div class="lab"><label for="res-days">Keep results (days)</label><InfoTip text={HELP.results} /></div>
            <input id="res-days" type="number" min="1" bind:value={s.retention.results_days} />
          </div>
        </div>
        <div class="disks">
          {#each [['Archive', store?.archive], ...(store?.same ? [] : [['Working folder', store?.work]])] as [label, d]}
            {#if d}
              <div class="disk">
                <div class="row small"><span>{label}</span><span class="muted push">{gb(d.free)} free of {gb(d.total)}</span></div>
                <div class="meter" class:low={d.free / d.total < 0.1}><div style:width="{(d.used / d.total) * 100}%"></div></div>
              </div>
            {/if}
          {/each}
        </div>
        <div class="row"><h3 class="sub-h">Cleanup</h3><InfoTip text={HELP.cleanup} /></div>
        {#if store}
          {#if store.items.length}
            <ul class="clean-list">
              {#each store.items as it}
                <li><span>{it.label}</span><span class="muted">{it.days.length ? `${it.days.length} day${it.days.length === 1 ? '' : 's'} · ` : ''}{gb(it.bytes)}</span></li>
              {/each}
            </ul>
          {:else}
            <p class="desc">Nothing to clean up under these limits.</p>
          {/if}
        {/if}
        <div class="row">
          <button class="btn" onclick={cleanNow} disabled={!store?.would_free || !!storeBusy}>Clean up now{store?.would_free ? ` (${gb(store.would_free)})` : ''}</button>
          <button class="btn quiet" onclick={() => loadStorage(true)} disabled={!!storeBusy}>Check again</button>
          {#if storeBusy}<span class="small muted">{storeBusy}</span>{/if}
        </div>
        <Switch label="Clean up automatically after each nightly run" bind:checked={s.retention.auto} />
        {#if store?.last_cleanup}<p class="desc">Last cleanup {store.last_cleanup.at.replace('T', ' ')}: freed {gb(store.last_cleanup.freed)}.</p>{/if}
        <p class="desc">Changed limits apply after <strong>Save changes</strong>; then <strong>Check again</strong>.</p>
      </section>

      <section class="panel" aria-labelledby="ni-h">
        <div class="row"><h2 id="ni-h">Nightly run</h2><InfoTip text={HELP.nightly} /></div>
        <Switch label="Process yesterday automatically" bind:checked={s.nightly.enabled} />
        <div class="cols2 inner">
          <div class="field"><label for="night-time">Start at</label><input id="night-time" type="time" bind:value={s.nightly.time} disabled={!s.nightly.enabled} /></div>
          <div class="field"><label for="catch-up">Also catch up on (days before)</label>
            <input id="catch-up" type="number" min="1" max="14" bind:value={s.nightly.catch_up_days} disabled={!s.nightly.enabled} /></div>
        </div>
        {#if night}
          <p class="desc">
            {#if night.enabled && night.next_run}Next run {new Date(night.next_run).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}.{/if}
            {#if night.last_run}Last run {night.last_run}{night.last_queued?.length ? `: queued ${night.last_queued.join('; ')}` : ': nothing needed doing'}.{/if}
          </p>
          {#if night.would_queue?.length}
            <p class="desc">Right now it would queue: {night.would_queue.map((w) => `${w.day} ${w.kind === 'run' ? 'everything' : w.kind} (${w.cameras.join(', ')})`).join('; ')}.</p>
          {:else if night.would_queue}
            <p class="desc">Right now every recent day is done or already queued.</p>
          {/if}
        {/if}
        <div class="row"><button class="btn" onclick={runNightNow} disabled={!night?.would_queue?.length}>Queue it now</button></div>
        <p class="desc">Turn this on in one Mac only: two Macs sharing the archive would both queue the same days.</p>
      </section>

      <section class="panel" id="updates" aria-labelledby="up-h">
        <div class="row">
          <h2 id="up-h">Updates</h2><InfoTip text={HELP.updates} />
          <span class="small muted">This is version {upd?.current}</span>
        </div>
        {#if upd?.newer}
          <div class="update">
            <div class="row"><strong>Version {upd.latest.version} is available</strong>
              {#if upd.latest.url}<a class="small" href={upd.latest.url} target="_blank" rel="noreferrer">on GitHub</a>{/if}</div>
            {#if upd.latest.notes}<p class="desc notes">{upd.latest.notes}</p>{/if}
            {#if upd.download.status === 'downloading'}
              <progress max={upd.download.total || 1} value={upd.download.done}></progress>
              <span class="small muted">Downloading {mb(upd.download.done)} of {mb(upd.download.total)}…</span>
            {:else if upd.download.status === 'done'}
              <p class="desc">Downloaded to <strong>{upd.download.path}</strong> and opened. Quit Reolapse (⌘Q; running jobs
                continue next time), drag Reolapse onto Applications and choose <strong>Replace</strong>, then open it again.</p>
            {:else if upd.latest.asset}
              <div class="row"><button class="btn primary" onclick={downloadUpdate}>Download {upd.latest.version} ({mb(upd.latest.asset.size)})</button>
                {#if upd.download.status === 'failed'}<span class="bad small">{upd.download.error}</span>{/if}</div>
            {:else}
              <p class="desc">This release has no Mac installer yet (it may still be building); check again in a few minutes.</p>
            {/if}
          </div>
        {:else if upd?.latest && !upd.error}
          <p class="desc">You have the latest version.</p>
        {/if}
        {#if upd?.error}<p class="desc bad-text">{upd.error}</p>{/if}
        <div class="row">
          <button class="btn" onclick={checkUpdates} disabled={checking || !upd?.token_set}>{checking ? 'Checking…' : 'Check for updates'}</button>
          {#if upd?.checked_at}<span class="small muted">Last checked {new Date(upd.checked_at * 1000).toLocaleString()}</span>{/if}
        </div>
        <Switch label="Check when Reolapse opens" bind:checked={s.updates.check_on_launch} />
        <div class="field">
          <div class="lab"><label for="gh-token">GitHub token</label></div>
          <div class="inline">
            <input id="gh-token" type="password" bind:value={token} autocomplete="off"
                   placeholder={upd?.token_set ? 'saved (paste a new one to replace)' : 'github_pat_…'} />
            <button class="btn" onclick={() => saveToken(token)} disabled={!token.trim()}>Save token</button>
            {#if upd?.token_set}<button class="btn" onclick={() => saveToken(null)}>Remove</button>{/if}
          </div>
          {#if updMsg}<span class="small muted">{updMsg}</span>{/if}
        </div>
        <p class="desc">Create one at <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noreferrer">GitHub → Fine-grained tokens</a>:
          repository access <strong>Only select repositories → {upd?.repo}</strong>, permission <strong>Contents: Read-only</strong>.
          It can read this one repository and nothing else.</p>
      </section>
    </div>
  {/if}
</div>

<style>
  .page { padding: 24px 32px 40px; display: flex; flex-direction: column; gap: 20px; max-width: 1180px; }
  header { display: flex; align-items: center; }
  h1 { font-size: 22px; font-weight: 600; }
  h2 { font-size: 16px; font-weight: 600; }
  .panel { display: flex; flex-direction: column; gap: 14px; }
  .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .push { margin-left: auto; gap: 12px; }
  .small { font-size: 13px; }
  .ok { color: #9BE7B1; margin-left: 8px; } .bad { color: var(--bad); margin-left: 8px; }
  .cols3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
  .cols2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: start; }
  .cols2.inner { gap: 14px; }
  .cam-head, .cam-row { display: grid; grid-template-columns: minmax(0, 1fr) auto 64px; gap: 16px; align-items: center; }
  .cam-head span:last-child { text-align: right; }
  .cam-row { min-height: 56px; border-top: 1px solid var(--line); }
  .cam-row :global(.row) { justify-content: flex-end; }
  .cam-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .name { font-size: 14px; }
  .warn { color: var(--warn); }
  .dim { color: var(--dim); }
  .switches { display: flex; flex-direction: column; }
  .switches :global(.row) { border-top: 1px solid var(--line); }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field label { font-size: 13px; color: var(--muted); }
  .lab { display: flex; align-items: center; gap: 2px; }
  .val { margin-left: auto; font-size: 13px; color: var(--text); }
  .sub-h { font-size: 13px; color: var(--muted); }
  .desc { margin: 0; font-size: 12.5px; line-height: 1.45; color: var(--muted); }
  .desc strong { color: var(--text); font-weight: 500; }
  input[readonly] { color: var(--muted); }
  .inline { display: flex; gap: 10px; }
  .inline input { flex: 1; min-width: 0; }
  input[type=range] { width: 100%; }
  .disks { display: flex; flex-direction: column; gap: 10px; }
  .meter { height: 8px; border-radius: 4px; background: var(--line); overflow: hidden; margin-top: 4px; }
  .meter div { height: 100%; background: var(--accent); }
  .meter.low div { background: var(--warn); }
  .clean-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
  .clean-list li { display: flex; justify-content: space-between; gap: 12px; }
  .update { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; }
  .notes { white-space: pre-wrap; max-height: 160px; overflow: auto; }
  .bad-text { color: var(--bad); }
  progress { width: 100%; }
</style>

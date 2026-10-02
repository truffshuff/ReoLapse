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
    density: 'Sets the automatic video length: roughly how many objects are on screen at once. Higher makes a shorter, busier video.',
    raw: 'Downloaded recordings are the biggest files (about 5–21 GB per camera per day). Days older than this would be deleted; the finished videos and stills are kept separately.',
    results: 'How long finished videos, stills and the object data behind them are kept.',
    nightly: 'Downloads and processes the previous day for every included camera, starting at this time.',
    cameras: 'Only fixed cameras work for synopsis: the background has to stay still. Tracking (pan/zoom) lenses are flagged.',
    work: 'Where backgrounds and object data are written and videos are rendered. These are many small, random reads and writes, so a folder on this Mac is much faster than the network share. Finished videos and stills are still copied to the archive, and days processed before you change this keep working from the share. About 1 GB per camera per day accumulates here.',
    nvr: 'The NVR address and login come from the project\'s .env file. Use a dedicated NVR user whose password has no & # % or spaces.',
  };

  let s = $state(null);
  let saved = $state(null);
  let health = $state(null);
  let nvr = $state(null);
  let models = $state([]);
  let test = $state(null);
  let testing = $state(false);
  let message = $state('');

  const dirty = $derived(s && saved && JSON.stringify(s) !== JSON.stringify(saved));
  const model = $derived(models.find((m) => m.file === s?.analysis.model));
  const size = $derived(SIZES.find((x) => x.value === Number(s?.analysis.imgsz)));

  onMount(async () => {
    [s, health, nvr, models] = await Promise.all([api.settings(), api.health(), api.nvr(), api.models()]);
    saved = structuredClone($state.snapshot(s));
  });

  async function save() {
    message = '';
    try {
      const out = await api.saveSettings($state.snapshot(s));
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
      return { name, hint, on: s.cameras.includes(name) };
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
        <div class="field"><label for="nvr-host">Address</label><input id="nvr-host" type="text" value={nvr?.host ?? ''} readonly /></div>
        <div class="field"><label for="nvr-user">Username</label><input id="nvr-user" type="text" value={nvr?.user ?? ''} readonly /></div>
        <div class="field"><label for="nvr-pass">Password</label><input id="nvr-pass" type="text" value={nvr?.password_set ? 'set' : 'not set'} readonly /></div>
      </div>
      <div><button class="btn" onclick={runTest} disabled={testing}>{testing ? 'Testing…' : 'Test connection'}</button></div>
    </section>

    <section class="panel" aria-labelledby="cam-h">
      <div class="row"><h2 id="cam-h">Cameras</h2><InfoTip text={HELP.cameras} />
        {#if !test?.ok}<span class="dim small">Test the connection to list every NVR camera.</span>{/if}</div>
      <div class="switches">
        {#each cameraRows as c (c.name)}
          <Switch label={c.name} hint={c.hint} checked={c.on} onchange={(on) => setCamera(c.name, on)} />
        {/each}
      </div>
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
          <input id="speed" type="range" min="1" max="4" step="0.5" bind:value={s.render.speed} />
        </div>
        <div class="field">
          <div class="lab"><label for="density">Objects on screen</label><InfoTip text={HELP.density} /><span class="val mono">~{s.render.density}</span></div>
          <input id="density" type="range" min="2" max="15" step="1" bind:value={s.render.density} />
        </div>
      </section>

      <section class="panel" aria-labelledby="st-h">
        <h2 id="st-h">Storage</h2>
        <div class="field"><label for="data-root">Archive (raw footage, finished videos)</label><input id="data-root" type="text" value={health?.data_root ?? ''} readonly /></div>
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
        <p class="desc">The archive is set in .env (REOLAPSE_DATA). Automatic cleanup isn't active yet.</p>
      </section>

      <section class="panel" aria-labelledby="ni-h">
        <div class="row"><h2 id="ni-h">Nightly run</h2><InfoTip text={HELP.nightly} /></div>
        <Switch label="Process yesterday automatically" bind:checked={s.nightly.enabled} />
        <div class="field"><label for="night-time">Start at</label><input id="night-time" type="time" bind:value={s.nightly.time} disabled={!s.nightly.enabled} /></div>
        <p class="desc">Saved now; the scheduler that acts on it arrives in a later update.</p>
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
  .switches { display: flex; flex-direction: column; }
  .switches :global(.row) { border-top: 1px solid var(--line); }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field label { font-size: 13px; color: var(--muted); }
  .lab { display: flex; align-items: center; gap: 2px; }
  .val { margin-left: auto; font-size: 13px; color: var(--text); }
  .desc { margin: 0; font-size: 12.5px; line-height: 1.45; color: var(--muted); }
  .desc strong { color: var(--text); font-weight: 500; }
  input[readonly] { color: var(--muted); }
  .inline { display: flex; gap: 10px; }
  .inline input { flex: 1; min-width: 0; }
  input[type=range] { width: 100%; }
</style>

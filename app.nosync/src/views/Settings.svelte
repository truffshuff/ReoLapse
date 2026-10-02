<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  const STYLES = ['cutout', 'ghost', 'outline'];
  const MOVING_LENS = /track|ptz/i; // TrackMix / TrackFlex lenses pan and zoom: no stable background

  let s = $state(null);
  let saved = $state(null);
  let health = $state(null);
  let nvr = $state(null);
  let test = $state(null);
  let testing = $state(false);
  let message = $state('');

  const dirty = $derived(s && saved && JSON.stringify(s) !== JSON.stringify(saved));

  onMount(async () => {
    [s, health, nvr] = await Promise.all([api.settings(), api.health(), api.nvr()]);
    saved = structuredClone($state.snapshot(s));
  });

  async function save() {
    message = '';
    try {
      const out = await api.saveSettings($state.snapshot(s));
      s = out;
      saved = structuredClone(out);
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

  function toggleCamera(name, on) {
    s.cameras = on ? [...s.cameras, name] : s.cameras.filter((c) => c !== name);
  }

  function toggleStyle(style, on) {
    s.render.styles = on ? STYLES.filter((x) => x === style || s.render.styles.includes(x)) : s.render.styles.filter((x) => x !== style);
  }

  // Cameras to offer: whatever the NVR reports (after a test), plus any already configured.
  const cameraRows = $derived.by(() => {
    if (!s) return [];
    const fromNvr = test?.ok ? test.channels : [];
    const names = [...new Set([...fromNvr.map((c) => c.name), ...s.cameras])];
    return names.map((name) => {
      const ch = fromNvr.find((c) => c.name === name);
      return { name, model: ch?.model ?? '', moving: ch ? MOVING_LENS.test(ch.model) : false, on: s.cameras.includes(name) };
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
        <h2 id="nvr-h">NVR connection</h2>
        {#if test?.ok}<span class="ok small">● Connected · {test.model}</span>{:else if test}<span class="bad small">● {test.error}</span>{/if}
      </div>
      <div class="cols3">
        <label class="field">Address<input type="text" value={nvr?.host ?? ''} readonly /></label>
        <label class="field">Username<input type="text" value={nvr?.user ?? ''} readonly /></label>
        <label class="field">Password<input type="text" value={nvr?.password_set ? 'set' : 'not set'} readonly /></label>
      </div>
      <div class="row">
        <button class="btn" onclick={runTest} disabled={testing}>{testing ? 'Testing…' : 'Test connection'}</button>
        <span class="dim small">Credentials are read from the project's .env file. Avoid &amp; # % and spaces in the password.</span>
      </div>
    </section>

    <section class="panel" aria-labelledby="cam-h">
      <div class="row"><h2 id="cam-h">Cameras</h2>{#if !test?.ok}<span class="dim small">Test the connection to list every NVR camera.</span>{/if}</div>
      {#each cameraRows as c (c.name)}
        <div class="cam-row">
          <span class="name">{c.name}</span>
          <span class="mono muted small">{c.model}</span>
          <span class="small" class:warn={c.moving}>{c.moving ? 'Lens moves (tracking): synopsis not supported' : ''}</span>
          <label class="check"><input type="checkbox" checked={c.on} onchange={(e) => toggleCamera(c.name, e.currentTarget.checked)} />Include</label>
        </div>
      {/each}
    </section>

    <div class="cols2">
      <section class="panel" aria-labelledby="an-h">
        <h2 id="an-h">Analysis</h2>
        <div class="cols2 inner">
          <label class="field">Frame rate
            <select bind:value={s.analysis.fps}><option value={5}>5 fps · faster</option><option value={10}>10 fps · smoother</option></select>
          </label>
          <label class="field">Where to look
            <select bind:value={s.analysis.types}><option value="all">Whole day</option><option value="person,animal,motion">NVR events only</option></select>
          </label>
          <label class="field">Model<input type="text" bind:value={s.analysis.model} /></label>
          <label class="field">Detector size<input type="number" min="640" max="1920" step="32" bind:value={s.analysis.imgsz} /></label>
        </div>
      </section>

      <section class="panel" aria-labelledby="re-h">
        <h2 id="re-h">Rendering defaults</h2>
        <div class="styles">
          {#each STYLES as st}
            <label class="check"><input type="checkbox" checked={s.render.styles.includes(st)} onchange={(e) => toggleStyle(st, e.currentTarget.checked)} />{st}</label>
          {/each}
        </div>
        <div class="cols2 inner">
          <label class="field">Color tags by
            <select bind:value={s.render.color}><option value="category">Type</option><option value="time">Time of day</option></select>
          </label>
          <label class="field">Allowed time shift (min)<input type="number" min="0" max="60" bind:value={s.render.drift} /></label>
          <label class="field">Object speed (× real time)<input type="number" min="1" max="8" step="0.5" bind:value={s.render.speed} /></label>
          <label class="field">Objects on screen (density)<input type="number" min="1" max="20" bind:value={s.render.density} /></label>
        </div>
      </section>

      <section class="panel" aria-labelledby="st-h">
        <h2 id="st-h">Storage</h2>
        <label class="field">Archive (raw footage, finished videos)<input type="text" value={health?.data_root ?? ''} readonly /></label>
        <label class="field">Working folder<input type="text" value={health?.work_root ?? ''} readonly /></label>
        <div class="cols2 inner">
          <label class="field">Keep raw footage (days)<input type="number" min="1" bind:value={s.retention.raw_days} /></label>
          <label class="field">Keep results (days)<input type="number" min="1" bind:value={s.retention.results_days} /></label>
        </div>
        <span class="dim small">Folders are set in .env (REOLAPSE_DATA, REOLAPSE_WORK). Automatic cleanup isn't active yet.</span>
      </section>

      <section class="panel" aria-labelledby="ni-h">
        <h2 id="ni-h">Nightly run</h2>
        <label class="check"><input type="checkbox" bind:checked={s.nightly.enabled} />Process yesterday automatically</label>
        <label class="field">Start at<input type="time" bind:value={s.nightly.time} /></label>
        <span class="dim small">Saved now; the scheduler that acts on it arrives in a later update.</span>
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
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .push { margin-left: auto; }
  .small { font-size: 13px; }
  .ok { color: #9BE7B1; } .bad { color: var(--bad); } .warn { color: var(--warn); }
  .cols3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
  .cols2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
  .cols2.inner { gap: 14px; }
  .cam-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.6fr) 110px; gap: 14px; align-items: center;
             min-height: 52px; border-top: 1px solid var(--line); }
  .name { font-weight: 500; }
  .check { display: flex; align-items: center; gap: 10px; min-height: 36px; font-size: 14px; text-transform: capitalize; }
  .styles { display: flex; gap: 18px; }
  input[readonly] { color: var(--muted); }
</style>

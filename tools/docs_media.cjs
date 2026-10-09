// Screenshots and short videos for the docs, taken from the synthetic demo (tools/make_demo.py).
//
//   uv run python tools/make_demo.py demo && (cd demo && REOLAPSE_DATA=archive REOLAPSE_WORK=work \
//     REOLAPSE_SETTINGS=$PWD/settings.json uv run reolapse render --day 2026-10-12 ...)
//   (cd demo && REOLAPSE_DATA=archive REOLAPSE_WORK=work REOLAPSE_SETTINGS=$PWD/settings.json \
//     REOLAPSE_APP_VERSION=demo REOLAPSE_UI_DIR=../app.nosync/dist uv run reolapse serve --port 8767) &
//   cd app.nosync && npx electron ../tools/docs_media.cjs ../docs/media http://127.0.0.1:8767
//
// Writes <out>/*.png screenshots and <out>/frames-<name>/ frame folders; tools/docs_media.sh turns
// those into .jpg / .mp4 / .gif.
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');

const out = process.argv[process.argv.length - 2];
const base = process.argv[process.argv.length - 1];
const DAY = '2026-10-12';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

process.on('unhandledRejection', (e) => { console.log('Error', e?.message ?? e); app.exit(1); });

app.whenReady().then(async () => {
  fs.mkdirSync(out, { recursive: true });
  const win = new BrowserWindow({ width: 1440, height: 900, show: false, backgroundColor: '#0E1014', paintWhenInitiallyHidden: true,
    webPreferences: { backgroundThrottling: false } });
  const js = (c) => win.webContents.executeJavaScript(c);
  await win.webContents.session.clearCache();
  const go = async (route, ms = 3500) => {
    await win.loadURL(`${base}/#/${route}`);
    await wait(ms);
    await js(CURSOR);
  };
  const size = (h) => win.setContentSize(1440, h);
  const shot = async (name, h = 900, rect) => {
    size(h); await wait(700);
    await js(`document.getElementById('__cursor')?.style.setProperty('visibility', 'hidden')`);
    const r = rect ? await js(rect) : undefined;
    fs.writeFileSync(path.join(out, `${name}.png`), (await win.webContents.capturePage(r)).toPNG());
    await js(`document.getElementById('__cursor')?.style.setProperty('visibility', 'visible')`);
    console.log('shot', name);
  };

  // A visible pointer for the videos: window.__point(selectorOrXY, {click, text}) glides there.
  const CURSOR = `(() => {
    if (window.__point) return;
    const c = document.createElement('div');
    c.id = '__cursor';
    c.style.cssText = 'position:fixed;z-index:99999;left:720px;top:450px;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:11px;'
      + 'background:rgba(255,255,255,0.85);border:2px solid #0E1014;box-shadow:0 2px 10px rgba(0,0,0,.5);pointer-events:none;transition:left .6s ease,top .6s ease,transform .15s';
    document.body.appendChild(c);
    window.__point = async (x, y, click) => {
      c.style.left = x + 'px'; c.style.top = y + 'px';
      await new Promise((r) => setTimeout(r, 650));
      if (click) { c.style.transform = 'scale(0.7)'; await new Promise((r) => setTimeout(r, 150)); c.style.transform = ''; }
    };
  })()`;
  const center = (sel, text) => js(`(() => { const els = [...document.querySelectorAll(${JSON.stringify(sel)})];
    const el = ${text ? `els.find(e => e.textContent.trim().startsWith(${JSON.stringify(text)}))` : 'els[0]'};
    if (!el) return null; el.scrollIntoView({ block: 'nearest' }); const r = el.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  const click = async (sel, text) => {
    const p = await center(sel, text);
    if (!p) { console.log('missing', sel, text); return; }
    await js(`window.__point(${p[0]}, ${p[1]}, true)`);
    await js(`(() => { const els = [...document.querySelectorAll(${JSON.stringify(sel)})];
      const el = ${text ? `els.find(e => e.textContent.trim().startsWith(${JSON.stringify(text)}))` : 'els[0]'}; el.click(); })()`);
  };
  const drag = async (sel, a, b, steps = 14) => {
    await js(`(async () => { const o = document.querySelector(${JSON.stringify(sel)}); const r = o.getBoundingClientRect();
      const P = (f) => [r.left + r.width * f[0], r.top + r.height * f[1]];
      const ev = (t, p) => o.dispatchEvent(new PointerEvent(t, { bubbles: true, pointerId: 1, button: 0, clientX: p[0], clientY: p[1] }));
      const A = P(${JSON.stringify(a)}), B = P(${JSON.stringify(b)});
      await window.__point(A[0], A[1], true); ev('pointerdown', A);
      for (let i = 1; i <= ${steps}; i++) { const p = [A[0] + (B[0] - A[0]) * i / ${steps}, A[1] + (B[1] - A[1]) * i / ${steps}];
        document.getElementById('__cursor').style.transition = 'none';
        document.getElementById('__cursor').style.left = p[0] + 'px'; document.getElementById('__cursor').style.top = p[1] + 'px';
        ev('pointermove', p); await new Promise((r) => setTimeout(r, 60)); }
      ev('pointerup', B);
      document.getElementById('__cursor').style.transition = 'left .6s ease,top .6s ease,transform .15s'; })()`);
  };

  // Video recorder: capture frames at ~10 fps while `script` runs.
  const record = async (name, script, h = 900) => {
    size(h); await wait(500);
    const dir = path.join(out, `frames-${name}`);
    fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir);
    let n = 0, on = true;
    const loop = (async () => { while (on) { const t = Date.now();
      const img = await win.webContents.capturePage(); fs.writeFileSync(path.join(dir, `${String(n++).padStart(5, '0')}.jpg`), img.resize({ width: 1440 }).toJPEG(88));
      await wait(Math.max(0, 100 - (Date.now() - t))); } })();
    await script();
    on = false; await loop;
    console.log('video', name, n, 'frames');
  };

  // ------------------------------------------------------------ screenshots
  await go('today'); await shot('today');
  await go(`player?day=${DAY}&camera=Driveway`, 4500);
  await js(`(() => { const b = document.querySelectorAll('.timeline button')[16]; b.click(); })()`); await wait(2500);
  await js(`document.querySelector('.stage video').pause()`); await wait(800);
  await shot('player', 1000);
  await click('[aria-label=Show] button', 'People'); await wait(2500);
  await js(`document.querySelector('.stage video').pause()`); await shot('player-people', 1000);
  await click('[aria-label=Show] button', 'Everything'); await wait(1500);
  await click('button', 'Find in area'); await wait(300);
  await drag('.overlay', [0.32, 0.5], [0.74, 0.98]); await wait(3000);
  await click('button', 'Done drawing'); await wait(600);
  await shot('find-in-area', 1250);
  await go(`multi?day=${DAY}`, 4000);
  await js(`(() => { const i = document.querySelector('input[type=time]'); i.value = '16:20'; i.dispatchEvent(new Event('input', { bubbles: true })); i.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await click('button', 'Go'); await wait(3500);
  await click('.result', ''); await wait(13000);
  await js(`document.querySelectorAll('video').forEach(v => v.pause())`); await shot('all-cameras', 1250);
  for (const [tab, name] of [['Trails', 'insights-trails'], ['Heatmap', 'insights-heatmap'], ['Group', 'insights-group']]) {
    await go(`insights?day=${DAY}&camera=Driveway`, 2500);
    await click('[role=tab]', tab); await wait(1200); await shot(name);
  }
  await go(`search?start=2026-10-06&end=${DAY}&after=22:00&before=05:00&types=person,animal`, 4000); await shot('search');
  await go('trends?range=7', 5000); await shot('trends', 1500);
  await go(`report?day=${DAY}&cams=Driveway,Front%20Yard&per=12`, 6000); await shot('report-preview', 1100);
  await go('jobs', 3000); await shot('jobs');
  await go('settings', 6000);
  const full = await js(`Math.min(3400, Math.max(...[...document.querySelectorAll('main, main *')].map(e => e.scrollHeight)) + 10)`);
  await shot('settings', full);
  const panel = (sel) => `(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: Math.floor(r.x) - 12, y: Math.floor(r.y) - 12, width: Math.ceil(r.width) + 24, height: Math.ceil(r.height) + 24 }; })()`;
  await shot('settings-updates', full, panel('#updates'));
  await shot('settings-storage', full, panel('[aria-labelledby="st-h"]'));
  await shot('settings-nightly', full, panel('[aria-labelledby="ni-h"]'));
  size(1400); await go('settings', 5000);
  await js(`[...document.querySelectorAll('.cam-text .linkish')][2].click()`); await wait(2500);
  await shot('settings-camera-more', 1400, `(() => { const b = document.querySelector('.cam-more').getBoundingClientRect(); return { x: Math.floor(b.x) - 12, y: Math.floor(b.y) - 80, width: Math.ceil(b.width) + 24, height: Math.ceil(b.height) + 92 }; })()`);

  // ------------------------------------------------------------ videos
  await go(`player?day=${DAY}&camera=Driveway`, 4500);
  await record('player', async () => {
    await click('.timeline button:nth-child(17)'); await wait(3500);
    await click('[aria-label=Style] button', 'ghost'); await wait(3000);
    await click('[aria-label=Show] button', 'People'); await wait(3000);
    await click('[aria-label=Show] button', 'Everything'); await wait(1500);
    await click('.stage [aria-label="Zoom in"]'); await wait(700);
    await click('.stage [aria-label="Zoom in"]'); await wait(2500);
    await click('.stage .lvl'); await wait(1500);
  });
  await go(`player?day=${DAY}&camera=Driveway`, 4500);
  await js(`document.querySelectorAll('.timeline button')[16].click()`); await wait(2000);
  await record('find-in-area', async () => {
    await click('button', 'Find in area'); await wait(600);
    await drag('.overlay', [0.32, 0.5], [0.74, 0.98]); await wait(2500);
    await click('button', 'Done drawing'); await wait(1000);
    await js(`document.querySelector('.found').scrollIntoView({ block: 'center' })`); await wait(1200);
    await click('.result'); await wait(4500);
  }, 1100);
  await go(`multi?day=${DAY}`, 4000);
  await js(`(() => { const i = document.querySelector('input[type=time]'); i.value = '16:20'; i.dispatchEvent(new Event('input', { bubbles: true })); i.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await click('button', 'Go'); await wait(3000);
  await record('all-cameras', async () => {
    await js(`document.querySelector('.events')?.scrollIntoView({ block: 'end' })`); await wait(1000);
    await click('.result'); await wait(14000);
  }, 1000);
  await go(`search?start=2026-10-06&end=${DAY}&types=person`, 3000);
  await record('search', async () => {
    await click('button', 'Night'); await wait(2500);
    await click('.result'); await wait(5000);
  });
  await go('trends?range=7', 4000);
  await record('trends', async () => {
    await click('[aria-label=Types] button', 'Vehicles'); await wait(2000);
    await click('[aria-label=Types] button', 'Animals'); await wait(2000);
    await js(`window.scrollTo({ top: 600, behavior: 'smooth' }); document.querySelector('main').scrollTo?.({ top: 600, behavior: 'smooth' })`); await wait(2500);
  });
  await go(`report?day=${DAY}&cams=Driveway&per=12`, 6000);
  await record('report', async () => {
    for (let y = 0; y <= 1800; y += 300) { await js(`window.scrollTo({ top: ${y}, behavior: 'smooth' })`); await wait(700); }
    await click('button', 'Draw an area'); await wait(1200);
    await js(`document.querySelector('.draw').scrollIntoView({ block: 'center' })`); await wait(800);
    await drag('.draw .overlay', [0.05, 0.62], [0.95, 0.72]); await wait(800);
    await js(`(() => { const i = document.querySelector('.areas-edit input[type=text]'); i.value = 'Street'; i.dispatchEvent(new Event('input', { bubbles: true })); })()`); await wait(600);
    await click('button', 'Save area'); await wait(2500);
  });
  app.quit();
});

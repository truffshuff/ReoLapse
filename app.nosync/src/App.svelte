<script>
  import { onMount } from 'svelte';
  import Icon from './components/Icon.svelte';
  import Today from './views/Today.svelte';
  import Player from './views/Player.svelte';
  import Multi from './views/Multi.svelte';
  import Insights from './views/Insights.svelte';
  import Jobs from './views/Jobs.svelte';
  import Settings from './views/Settings.svelte';
  import Report from './views/Report.svelte';
  import Search from './views/Search.svelte';
  import Trends from './views/Trends.svelte';
  import { api, onJobEvents } from './lib/api.js';
  import { jobState } from './lib/busy.svelte.js';

  const NAV = [
    { id: 'today', label: 'Today' },
    { id: 'player', label: 'Player' },
    { id: 'multi', label: 'All cameras' },
    { id: 'insights', label: 'Insights' },
    { id: 'search', label: 'Search' },
    { id: 'trends', label: 'Trends' },
    { id: 'jobs', label: 'Jobs' },
    { id: 'settings', label: 'Settings' },
  ];

  // Hash routing: #/player?day=2026-10-01&camera=Garage
  function parse() {
    const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
    const known = NAV.some((n) => n.id === path) || path === 'report';
    return { view: known ? path : 'today', params: Object.fromEntries(new URLSearchParams(query)) };
  }
  let route = $state(parse());
  let activeJobs = $state(0);
  let nvrOk = $state(null);
  let update = $state(null);

  onMount(() => {
    const onHash = () => (route = parse());
    window.addEventListener('hashchange', onHash);
    const jobs = new Map();
    const recount = () => (activeJobs = [...jobs.values()].filter((j) => j.status === 'queued' || j.status === 'running').length);
    const stop = onJobEvents({
      snapshot: (list) => { jobs.clear(); list.forEach((j) => jobs.set(j.id, j)); recount(); },
      job: (e) => { jobs.set(e.job.id, e.job); recount(); },
    });
    api.health().then((h) => (nvrOk = h.data_root_available)).catch(() => (nvrOk = false));
    api.settings().then((st) => st.updates?.check_on_launch && api.update(true))
      .then((u) => u?.newer && (update = u.latest.version)).catch(() => {});
    return () => { window.removeEventListener('hashchange', onHash); stop(); };
  });
</script>

<div class="shell" class:bare={route.view === 'report' && route.params.print === '1'}>
  <nav aria-label="Main">
    <div class="brand"><span class="logo"><Icon name="logo" size={24} /></span>Reolapse</div>
    {#each NAV as item}
      <a href={'#/' + item.id} class:active={route.view === item.id} aria-current={route.view === item.id ? 'page' : undefined}>
        <Icon name={item.id} />{item.label}
        {#if item.id === 'jobs' && jobState.paused}<span class="count paused">paused</span>
        {:else if item.id === 'jobs' && activeJobs}<span class="count mono">{activeJobs}</span>{/if}
      </a>
    {/each}
    {#if update}<a class="update" href="#/settings">Update available: {update}</a>{/if}
    <div class="status">
      <div class="row"><span class="dot" style:background={nvrOk ? 'var(--ok)' : nvrOk === false ? 'var(--bad)' : 'var(--dim)'}></span>
        {nvrOk ? 'Storage connected' : nvrOk === false ? 'Storage unavailable' : 'Checking…'}</div>
    </div>
  </nav>

  <main>
    {#key route.view}
      {#if route.view === 'today'}<Today />
      {:else if route.view === 'player'}<Player params={route.params} />
      {:else if route.view === 'multi'}<Multi params={route.params} />
      {:else if route.view === 'insights'}<Insights params={route.params} />
      {:else if route.view === 'jobs'}<Jobs />
      {:else if route.view === 'settings'}<Settings />
      {:else if route.view === 'report'}<Report params={route.params} />
      {:else if route.view === 'search'}<Search params={route.params} />
      {:else if route.view === 'trends'}<Trends params={route.params} />
      {/if}
    {/key}
  </main>
</div>

<style>
  .shell { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100%; }
  .shell.bare { display: block; }
  .shell.bare nav { display: none; }
  nav {
    display: flex; flex-direction: column; gap: 4px; padding: 20px 14px; background: var(--rail);
    border-right: 1px solid var(--line); position: sticky; top: 0; height: 100vh;
    -webkit-app-region: drag; /* lets the window be dragged by the sidebar (hidden title bar) */
  }
  nav a, nav .status { -webkit-app-region: no-drag; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 26px 10px 22px; font-weight: 600; font-size: 17px; }
  .logo { color: var(--accent); display: flex; }
  nav a {
    display: flex; align-items: center; gap: 12px; min-height: 44px; padding: 0 12px; border-radius: 8px; color: var(--muted);
  }
  nav a:hover { color: var(--text); }
  nav a.update { margin-top: auto; color: var(--accent-text); font-size: 13px; }
  nav a.update + .status { margin-top: 0; }
  nav a.active { background: #1E2430; color: var(--text); font-weight: 500; }
  .count { margin-left: auto; font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--accent-soft); color: var(--accent-text); }
  .count.paused { background: #3A3122; color: var(--warn); }
  .status { margin-top: auto; padding: 14px 12px; border-radius: 10px; background: #171B23; border: 1px solid var(--line); font-size: 13px; color: var(--muted); }
  .status .row { display: flex; align-items: center; gap: 8px; }
  main { min-width: 0; }
</style>

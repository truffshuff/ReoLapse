<script>
  // Overlay for a <video>: "Loading footage…" until the first frame, a slow-network note after a
  // few seconds, or the error. Place inside a position:relative container next to the video.
  let { status = 'loading', busy = false } = $props(); // loading | ready | error

  let slow = $state(false);
  $effect(() => {
    slow = false;
    if (status !== 'loading') return;
    const t = setTimeout(() => (slow = true), 5000);
    return () => clearTimeout(t);
  });
</script>

{#if status !== 'ready'}
  <div class="vs" role="status" aria-live="polite">
    {#if status === 'error'}
      <span class="err">Couldn't load this footage.</span>
    {:else}
      <span class="spin" aria-hidden="true"></span>
      <span>Loading footage…</span>
      {#if slow}
        <span class="note">{busy ? 'A job is downloading to the share right now, so footage loads slowly.' : 'Still loading; the network share may be busy.'}</span>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .vs { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
        background: rgba(11, 13, 17, 0.72); font-size: 14px; color: var(--text); pointer-events: none; text-align: center; padding: 12px; }
  .note { font-size: 12.5px; color: var(--muted); max-width: 360px; }
  .err { color: var(--bad); }
  .spin { width: 22px; height: 22px; border-radius: 50%; border: 2px solid var(--line-strong); border-top-color: var(--accent);
          animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

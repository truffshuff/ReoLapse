<script>
  // ✎ on an object card: fix its type or hide it ("not an object"). Applies to counts, charts, reports
  // and searches right away, and to videos at their next render.
  import { api } from '../lib/api.js';

  let { day, camera, obj, onchange = () => {} } = $props();
  let open = $state(false);
  let busy = $state(false);
  let err = $state('');

  const CHOICES = [['person', 'Person'], ['vehicle', 'Vehicle'], ['animal', 'Animal'], ['hidden', 'Not an object']];

  async function set(value) {
    busy = true;
    err = '';
    try {
      await api.correct(day, camera, obj.id, value);
      open = false;
      onchange(obj.id, value);
    } catch (e) {
      err = e.message;
    }
    busy = false;
  }
</script>

<span class="wrap">
  <button type="button" class="pen" class:fixed={obj.corrected} aria-label="Correct this object" aria-expanded={open}
          title={obj.corrected ? 'Corrected by hand: click to change' : 'Wrong type, or not an object? Correct it'}
          onclick={(e) => { e.stopPropagation(); open = !open; }}>✎</button>
  {#if open}
    <div class="menu" role="menu" tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.key === 'Escape' && (open = false)}>
      <div class="hd">This is…</div>
      {#each CHOICES as [v, label]}
        <button type="button" role="menuitem" disabled={busy || (v === obj.category && obj.corrected)} onclick={() => set(v)}>{label}</button>
      {/each}
      {#if obj.corrected}<button type="button" role="menuitem" class="undo" disabled={busy} onclick={() => set(null)}>Undo correction</button>{/if}
      <div class="note">Counts and reports update now; videos at their next render.</div>
      {#if err}<div class="err">{err}</div>{/if}
    </div>
  {/if}
</span>

<style>
  .wrap { position: absolute; top: 4px; right: 4px; z-index: 3; }
  .pen { width: 24px; height: 24px; padding: 0; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.15);
         background: rgba(14, 16, 20, 0.7); color: #E8EBF0; font-size: 13px; line-height: 22px; cursor: pointer; opacity: 0; }
  :global(*:hover) > .wrap .pen, .pen:focus-visible, .pen.fixed, .pen[aria-expanded='true'] { opacity: 1; }
  .pen.fixed { background: var(--accent); color: var(--bg); border-color: transparent; }
  .menu { position: absolute; right: 0; top: 28px; width: 190px; padding: 6px; border-radius: 10px; background: #1C212B;
          border: 1px solid #333B4A; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5); display: flex; flex-direction: column; gap: 2px; }
  .hd { font-size: 11px; color: var(--muted); padding: 2px 6px 4px; }
  .menu button { text-align: left; padding: 6px 8px; border: 0; border-radius: 6px; background: transparent; color: var(--text); font-size: 13px; cursor: pointer; }
  .menu button:hover:not(:disabled) { background: rgba(142, 168, 255, 0.14); }
  .menu button:disabled { opacity: 0.45; cursor: default; }
  .menu .undo { color: var(--muted); }
  .note { font-size: 11px; color: var(--dim); padding: 4px 6px 2px; line-height: 1.35; }
  .err { font-size: 12px; color: var(--bad); padding: 2px 6px; }
</style>

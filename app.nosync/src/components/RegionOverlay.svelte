<script>
  // Draw rectangles over the video (normalised 0..1 coordinates). Drawn boxes stay visible while
  // playing; only in `drawing` mode does the overlay take the mouse (so video controls keep working).
  let { regions = $bindable([]), drawing = false } = $props();

  let el = $state();
  let draft = $state(null);

  function point(e) {
    const r = el.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  }

  function down(e) {
    if (!drawing || e.button !== 0) return;
    const p = point(e);
    draft = { ax: p.x, ay: p.y, bx: p.x, by: p.y };
    el.setPointerCapture(e.pointerId);
  }

  function move(e) {
    if (!draft) return;
    const p = point(e);
    draft = { ...draft, bx: p.x, by: p.y };
  }

  function up() {
    if (!draft) return;
    const r = { x0: Math.min(draft.ax, draft.bx), y0: Math.min(draft.ay, draft.by), x1: Math.max(draft.ax, draft.bx), y1: Math.max(draft.ay, draft.by) };
    draft = null;
    if (r.x1 - r.x0 > 0.01 && r.y1 - r.y0 > 0.02) regions = [...regions, r];
  }

  const box = (r) => `left:${r.x0 * 100}%;top:${r.y0 * 100}%;width:${(r.x1 - r.x0) * 100}%;height:${(r.y1 - r.y0) * 100}%`;
  const draftBox = $derived(draft && box({ x0: Math.min(draft.ax, draft.bx), y0: Math.min(draft.ay, draft.by),
                                          x1: Math.max(draft.ax, draft.bx), y1: Math.max(draft.ay, draft.by) }));
</script>

<div bind:this={el} class="overlay" class:drawing role="presentation"
     onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={() => (draft = null)}>
  {#each regions as r, i}
    <div class="region" style={box(r)}>
      <span class="num">{i + 1}</span>
      <button type="button" class="x" aria-label="Remove area {i + 1}" onpointerdown={(e) => e.stopPropagation()}
              onclick={() => (regions = regions.filter((_, j) => j !== i))}>×</button>
    </div>
  {/each}
  {#if draftBox}<div class="region draft" style={draftBox}></div>{/if}
  {#if drawing && !regions.length && !draft}<div class="hint">Drag to draw an area; add as many as you like</div>{/if}
</div>

<style>
  .overlay { position: absolute; inset: 0; pointer-events: none; }
  .overlay.drawing { pointer-events: auto; cursor: crosshair; background: rgba(14, 16, 20, 0.15); }
  .region { position: absolute; border: 2px solid var(--accent); border-radius: 4px; background: rgba(142, 168, 255, 0.12); }
  .region.draft { border-style: dashed; }
  .num { position: absolute; left: 4px; top: 2px; font-size: 12px; font-weight: 600; color: var(--accent); }
  .x { position: absolute; right: -12px; top: -12px; width: 24px; height: 24px; border-radius: 12px; border: 0; padding: 0;
       background: var(--accent); color: var(--bg); font-weight: 700; line-height: 24px; pointer-events: auto; }
  .hint { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); padding: 8px 14px; border-radius: 8px;
          background: rgba(14, 16, 20, 0.85); font-size: 14px; pointer-events: none; }
</style>

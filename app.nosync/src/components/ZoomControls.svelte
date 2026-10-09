<script>
  // − / level / + buttons in the corner of a zoomable video (see lib/zoom.svelte.js).
  import { MAX_ZOOM } from '../lib/zoom.svelte.js';

  let { zoom, disabled = false } = $props();
</script>

<div class="zoom" class:on={zoom.z > 1} role="group" aria-label="Zoom">
  <button type="button" aria-label="Zoom out" disabled={disabled || zoom.z <= 1} onclick={() => zoom.zoomAt(1 / 1.5)}>−</button>
  <button type="button" class="lvl" aria-label="Reset zoom" title="Reset zoom (pinch or ⌘-scroll to zoom, drag to move)"
          disabled={zoom.z <= 1} onclick={() => zoom.reset()}>{zoom.z > 1 ? `${zoom.z.toFixed(1)}×` : '1×'}</button>
  <button type="button" aria-label="Zoom in" disabled={disabled || zoom.z >= MAX_ZOOM} onclick={() => zoom.zoomAt(1.5)}>+</button>
</div>

<style>
  .zoom { position: absolute; top: 10px; right: 10px; z-index: 4; display: flex; gap: 2px; padding: 3px; border-radius: 10px;
          background: rgba(14, 16, 20, 0.72); border: 1px solid rgba(255, 255, 255, 0.12); opacity: 0; transition: opacity 0.15s; }
  :global(*:hover) > .zoom, .zoom.on, .zoom:focus-within { opacity: 1; }
  button { min-width: 30px; height: 28px; padding: 0 6px; border: 0; border-radius: 7px; background: transparent; color: #E8EBF0;
           font-size: 15px; font-weight: 600; cursor: pointer; }
  button.lvl { font-size: 12px; font-variant-numeric: tabular-nums; min-width: 40px; }
  button:hover:not(:disabled) { background: rgba(255, 255, 255, 0.12); }
  button:disabled { opacity: 0.4; cursor: default; }
</style>

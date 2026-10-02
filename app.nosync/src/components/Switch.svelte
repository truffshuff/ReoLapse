<script>
  // Accessible on/off switch (role="switch"); the whole row is the click target.
  // `compact` draws only the track (the caller shows the label); `label` is then the accessible name.
  let { checked = $bindable(false), label, hint = '', disabled = false, compact = false, onchange } = $props();

  function toggle() {
    if (disabled) return;
    checked = !checked;
    onchange?.(checked);
  }
</script>

<button type="button" class="row" class:compact role="switch" aria-checked={checked} aria-label={compact ? label : undefined}
        {disabled} onclick={toggle}>
  {#if !compact}
    <span class="text">
      <span class="label">{label}</span>
      {#if hint}<span class="hint">{hint}</span>{/if}
    </span>
  {/if}
  <span class="track" class:on={checked} aria-hidden="true"><span class="knob"></span></span>
</button>

<style>
  .row { display: flex; align-items: center; gap: 14px; width: 100%; min-height: 44px; padding: 4px 0; border: 0;
         background: transparent; color: var(--text); text-align: left; }
  .text { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
  .label { font-size: 14px; text-transform: none; }
  .hint { font-size: 12px; color: var(--muted); }
  .track { flex: none; position: relative; width: 44px; height: 26px; border-radius: 13px; background: #3A4252; transition: background 0.15s; }
  .track.on { background: var(--accent); }
  .knob { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 10px; background: var(--text); transition: left 0.15s; }
  .track.on .knob { left: 21px; }
  .row.compact { width: auto; }
  .row:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 6px; }
</style>

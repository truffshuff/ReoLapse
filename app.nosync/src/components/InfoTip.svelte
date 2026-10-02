<script>
  // Small ⓘ button whose explanation shows on hover or keyboard focus (and on click, for touch).
  let { text, label = 'More information' } = $props();
  const id = `tip-${Math.random().toString(36).slice(2, 9)}`;
  let open = $state(false);
</script>

<span class="wrap" role="presentation" onmouseenter={() => (open = true)} onmouseleave={() => (open = false)}>
  <button type="button" class="i" aria-label={label} aria-describedby={id} aria-expanded={open}
          onfocus={() => (open = true)} onblur={() => (open = false)} onclick={(e) => { e.preventDefault(); open = !open; }}>
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <circle cx="12" cy="12" r="10"></circle><path d="M12 11v6M12 7.5v.5"></path>
    </svg>
  </button>
  <span {id} role="tooltip" class="tip" class:open>{text}</span>
</span>

<style>
  .wrap { position: relative; display: inline-flex; vertical-align: middle; }
  .i { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; padding: 0; border: 0;
       border-radius: 12px; background: transparent; color: var(--dim); }
  .i:hover, .i:focus-visible { color: var(--accent); }
  .tip { position: absolute; z-index: 20; left: 50%; bottom: calc(100% + 6px); transform: translateX(-50%); width: max-content;
         max-width: 320px; padding: 10px 12px; border-radius: 8px; background: #252B37; border: 1px solid #3A4252;
         color: var(--text); font-size: 13px; font-weight: 400; line-height: 1.45; text-transform: none; white-space: normal;
         box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4); visibility: hidden; opacity: 0; transition: opacity 0.12s; pointer-events: none; }
  .tip.open { visibility: visible; opacity: 1; }
</style>

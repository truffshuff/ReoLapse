<script>
  let { name, size = 18 } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if name === 'today'}
    <rect x="3" y="4" width="18" height="17" rx="2"></rect><path d="M3 9h18M8 2v4M16 2v4"></path>
  {:else if name === 'player'}
    <rect x="2" y="5" width="20" height="14" rx="2"></rect><path d="M10 9l5 3-5 3z"></path>
  {:else if name === 'multi'}
    <rect x="2" y="4" width="13" height="7" rx="1.5"></rect><rect x="2" y="13" width="13" height="7" rx="1.5"></rect><rect x="17" y="4" width="5" height="16" rx="1.5"></rect>
  {:else if name === 'insights'}
    <path d="M3 17c4-8 7 2 10-4s5-6 8-6"></path><path d="M3 21h18"></path>
  {:else if name === 'search'}
    <circle cx="10.5" cy="10.5" r="6.5"></circle><path d="M15.5 15.5L21 21"></path>
  {:else if name === 'trends'}
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2"></path>
  {:else if name === 'jobs'}
    <path d="M4 6h16M4 12h10M4 18h7"></path><circle cx="18" cy="16" r="3"></circle>
  {:else if name === 'settings'}
    <circle cx="12" cy="12" r="3"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"></path>
  {:else if name === 'play'}
    <path d="M8 5l12 7-12 7z" fill="currentColor" stroke="none"></path>
  {:else if name === 'pause'}
    <rect x="6" y="5" width="4" height="14" fill="currentColor" stroke="none"></rect><rect x="14" y="5" width="4" height="14" fill="currentColor" stroke="none"></rect>
  {:else if name === 'prev'}
    <path d="M15 6l-6 6 6 6"></path>
  {:else if name === 'next'}
    <path d="M9 6l6 6-6 6"></path>
  {:else if name === 'logo'}
    <circle cx="12" cy="12" r="10"></circle><path d="M12 5.5v6.5l4.5 2.7"></path>
  {/if}
</svg>

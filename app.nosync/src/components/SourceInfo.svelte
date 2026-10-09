<script>
  // Which footage and settings a day's results came from: the stream on the share, how objects were
  // found (recorded since v0.1.12) and, for a video, how it was rendered. Warns when the day was
  // analyzed on a different stream than the footage now downloaded.
  import { STREAM_NAME, analyzedFrom, analyzedWith, when } from '../lib/format.js';

  let { footage = null, analysis = null, analyzed = true, render = null } = $props();

  const stream = $derived(footage?.stream ? STREAM_NAME[footage.stream] : '');
  const partial = $derived(footage && footage.files && footage.downloaded < footage.files);
  const mismatch = $derived(analysis?.stream && footage?.stream && footage.stream !== 'mixed' && analysis.stream !== footage.stream);
  const tags = $derived(render ? [render.style, render.color === 'time' ? 'tags by time' : null,
    render.drift_min != null ? `±${render.drift_min} min` : null, render.speed ? `${render.speed}× speed` : null,
    render.density ? `~${render.density} on screen` : null,
    render.clock === 'adaptive' ? 'adaptive clock' : render.clock === 'even' ? 'even clock' : null].filter(Boolean).join(' · ') : '');
</script>

<dl class="info small">
  {#if stream}
    <div><dt>Footage</dt><dd>{stream}{#if partial}<span class="warn"> · {footage.downloaded} of {footage.files} files downloaded</span>{/if}</dd></div>
  {/if}
  {#if analysis?.inferred}
    <div><dt>Objects found in</dt><dd>{analysis.stream ? analyzedFrom(analysis) : `${analysis.size[0]}×${analysis.size[1]} frames`}<span class="dim">{' · model and other settings not recorded (analyzed before v0.1.12)'}</span></dd></div>
  {:else if analysis}
    <div><dt>Objects found in</dt><dd>{analyzedFrom(analysis)} · {analyzedWith(analysis)}{#if analysis.finished}<span class="dim"> · {when(analysis.finished)}</span>{/if}</dd></div>
  {:else if analyzed}
    <div><dt>Objects found in</dt><dd class="dim">settings not recorded (analyzed before v0.1.12)</dd></div>
  {/if}
  {#if render}
    <div><dt>Video</dt><dd>{tags}{#if render.rendered}<span class="dim"> · {when(render.rendered)}</span>{/if}</dd></div>
  {/if}
  {#if mismatch}
    <div class="warn">Analyzed on {STREAM_NAME[analysis.stream]}, but the footage now downloaded is {stream}: run
      <a href="#/jobs">Find moving objects</a> again to use it.</div>
  {/if}
</dl>

<style>
  .info { display: flex; flex-wrap: wrap; gap: 4px 18px; margin: 0; color: var(--muted); }
  .info div { display: flex; gap: 6px; }
  dt { color: var(--dim); }
  dd { margin: 0; color: var(--text); }
  .warn { color: var(--warn); }
  .warn a { color: inherit; text-decoration: underline; }
</style>

// Zoom and pan for a <video> without touching its native controls: the picture is cropped with CSS
// object-view-box, so the element (and its control bar) keeps its size. Pinch on a trackpad or
// ⌘/Ctrl + scroll zooms toward the pointer; drag pans while zoomed; the buttons step and reset.

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const MAX_ZOOM = 8;

export class Zoom {
  z = $state(1); // 1 = whole picture
  cx = $state(0.5); // centre of the view, 0..1 of the picture
  cy = $state(0.5);

  /** The visible part of the picture: { x, y, w } (w is both width and height, as a fraction). */
  get view() {
    const w = 1 / this.z;
    return { x: clamp(this.cx - w / 2, 0, 1 - w), y: clamp(this.cy - w / 2, 0, 1 - w), w };
  }

  /** Zoom by `factor`, keeping the picture point under (px, py) (0..1 of the element) in place. */
  zoomAt(factor, px = 0.5, py = 0.5) {
    const v = this.view;
    const u = v.x + px * v.w, t = v.y + py * v.w; // picture point under the pointer
    const z = clamp(this.z * factor, 1, MAX_ZOOM), w = 1 / z;
    this.z = z;
    this.cx = clamp(u - px * w + w / 2, w / 2, 1 - w / 2);
    this.cy = clamp(t - py * w + w / 2, w / 2, 1 - w / 2);
  }

  panBy(dx, dy) { // in fractions of the element
    const w = 1 / this.z;
    this.cx = clamp(this.cx - dx * w, w / 2, 1 - w / 2);
    this.cy = clamp(this.cy - dy * w, w / 2, 1 - w / 2);
  }

  reset() { this.z = 1; this.cx = 0.5; this.cy = 0.5; }

  /** Element fraction (0..1) -> picture fraction, and back (for overlays drawn on a zoomed video). */
  toPicture(px, py) { const v = this.view; return { x: v.x + px * v.w, y: v.y + py * v.w }; }
}

/** The crop for the video: <video style:object-view-box={viewBox(zoom)} …> (set in markup, so it also
 *  applies to a video that appears or is replaced later). */
export function viewBox(zoom) {
  if (zoom.z <= 1) return null;
  const { x, y, w } = zoom.view;
  return `inset(${(y * 100).toFixed(3)}% ${((1 - x - w) * 100).toFixed(3)}% ${((1 - y - w) * 100).toFixed(3)}% ${(x * 100).toFixed(3)}%)`;
}

/** Attach to the element around a <video>: {@attach zoomable(zoom)} handles pinch / ⌘-scroll and
 *  drag-to-pan. `enabled()` can switch it off (e.g. while drawing areas). */
export function zoomable(zoom, enabled = () => true) {
  return (node) => {
    const video = () => node.querySelector('video');
    let drag = null, moved = false;

    const rel = (e) => {
      const r = video()?.getBoundingClientRect() ?? node.getBoundingClientRect();
      return { px: (e.clientX - r.left) / r.width, py: (e.clientY - r.top) / r.height, r };
    };
    const onWheel = (e) => {
      if (!enabled() || !(e.ctrlKey || e.metaKey)) return; // a plain scroll still scrolls the page
      e.preventDefault();
      const { px, py } = rel(e);
      zoom.zoomAt(Math.exp(-e.deltaY * 0.01), clamp(px, 0, 1), clamp(py, 0, 1));
    };
    const onDown = (e) => {
      if (!enabled() || zoom.z <= 1 || e.button !== 0 || e.target !== video()) return;
      const { py, r } = rel(e);
      if (py * r.height > r.height - 48) return; // leave the native control bar alone
      drag = { x: e.clientX, y: e.clientY, w: r.width, h: r.height };
      moved = false;
    };
    const onMove = (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      zoom.panBy(dx / drag.w, dy / drag.h);
      drag.x = e.clientX; drag.y = e.clientY;
    };
    const onUp = () => { drag = null; };
    const onClick = (e) => { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }; // a pan isn't play/pause

    node.addEventListener('wheel', onWheel, { passive: false });
    node.addEventListener('pointerdown', onDown);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    node.addEventListener('click', onClick, true);
    return () => {
      node.removeEventListener('wheel', onWheel);
      node.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      node.removeEventListener('click', onClick, true);
    };
  };
}

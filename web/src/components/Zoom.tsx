// Zoom and pan for a workflow graph: − / + / Fit buttons, Ctrl/⌘ + wheel (trackpad pinch), drag an empty part to pan.
// Each place keeps its own zoom (per browser) under `keel2.zoom.<id>`.

import { useEffect, useRef, useState, type ReactNode } from "react";

export const ZOOM_MIN = 0.4;
export const ZOOM_MAX = 3;
const STEP = 1.25;

const clamp = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 100) / 100));

function load(id: string): number {
  try {
    const v = Number(localStorage.getItem(`keel2.zoom.${id}`));
    return v ? clamp(v) : 1;
  } catch {
    return 1;
  }
}

function save(id: string, z: number) {
  try {
    localStorage.setItem(`keel2.zoom.${id}`, String(z));
  } catch {
    /* private window: zoom just isn't remembered */
  }
}

export function Zoom({ id, children }: { id: string; children: ReactNode }) {
  const [zoom, setZoomState] = useState(() => load(id));
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  // Zoom around a point of the box (the pointer for the wheel, the middle for the buttons), so what you look at stays put.
  const setZoom = (next: number, at?: { x: number; y: number }) => {
    const el = box.current;
    const z = clamp(next);
    if (el && z !== zoom) {
      const r = el.getBoundingClientRect();
      const px = at ? at.x - r.left : el.clientWidth / 2;
      const py = at ? at.y - r.top : el.clientHeight / 2;
      const k = z / zoom;
      requestAnimationFrame(() => {
        el.scrollLeft = (el.scrollLeft + px) * k - px;
        el.scrollTop = (el.scrollTop + py) * k - py;
      });
    }
    setZoomState(z);
    save(id, z);
  };

  // React's onWheel is passive, so preventDefault (stop the page zooming) needs a real listener.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setZoom(zoom * Math.exp(-e.deltaY * 0.0025), { x: e.clientX, y: e.clientY });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // Only an empty part of the graph pans; a node or a button keeps its click.
    const t = e.target as Element;
    if (e.button !== 0 || t.closest(".node, button, a, input, textarea, select, .plus")) return;
    const el = box.current!;
    // v0.15.3 a press on the box's own scroll bar scrolls, it does not pan
    const r = el.getBoundingClientRect();
    const barY = el.offsetWidth - el.clientWidth > 0 && e.clientX - r.left > el.clientWidth;
    const barX = el.offsetHeight - el.clientHeight > 0 && e.clientY - r.top > el.clientHeight;
    if (barX || barY) return;
    drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
    el.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    box.current!.scrollLeft = d.left - (e.clientX - d.x);
    box.current!.scrollTop = d.top - (e.clientY - d.y);
  };
  const stop = () => { drag.current = null; };

  return (
    <div className="zoomer">
      <div className="zoombar" role="group" aria-label="Zoom">
        <button type="button" className="btn sm" aria-label="Zoom out" disabled={zoom <= ZOOM_MIN} onClick={() => setZoom(zoom / STEP)}>−</button>
        <span className="zoomlvl" aria-live="polite">{Math.round(zoom * 100)}%</span>
        <button type="button" className="btn sm" aria-label="Zoom in" disabled={zoom >= ZOOM_MAX} onClick={() => setZoom(zoom * STEP)}>+</button>
        <button type="button" className="btn sm" aria-label="Fit to width" disabled={zoom === 1} onClick={() => setZoom(1)}>Fit</button>
      </div>
      <div ref={box} className={`graph-wrap zoombox ${zoom > 1 ? "pannable" : ""}`} data-testid={`zoom-${id}`}
        style={{ "--zoom": zoom } as React.CSSProperties}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={stop} onPointerCancel={stop}>
        {children}
      </div>
    </div>
  );
}

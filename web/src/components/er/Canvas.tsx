// The diagram canvas, like an IDE's: drag empty space (or hold Space, or the middle button) to pan, the wheel or two
// fingers scroll, Ctrl/⌘ + wheel or a pinch zooms around the pointer, and a minimap in the corner shows where you are.
// The scene is passed in as children and only moves with a transform, so panning never re-renders it.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type View = { x: number; y: number; k: number };
export type Box = { x: number; y: number; w: number; h: number };
export type CanvasHandle = {
  fit(animate?: boolean): void;
  /** The opening view: the whole drawing when it is readable that small, else the `start` box at a reading size. */
  home(animate?: boolean): void;
  zoomBy(f: number): void;
  zoomTo(k: number): void;
  /** Bring a box into view (centred), keeping the zoom unless it is too small to read. */
  reveal(b: Box, force?: boolean): void;
  view(): View;
  el(): HTMLDivElement | null;
};

export const K_MAX = 3;
const FALLBACK = { w: 960, h: 600 };
const MINI_PAD = 20;
const READABLE = 0.5;

function reduced() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

type Props = {
  /** The drawing's bounds in world coordinates (x / y may be negative after boxes were dragged). */
  world: { x?: number; y?: number; w: number; h: number };
  label: string;
  describedBy?: string;
  children: ReactNode;
  minimap?: ReactNode;
  onView?: (v: View) => void;
  onBackground?: () => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => void;
  /** A box drag owns the pointer: the canvas must not pan. */
  grabbing?: boolean;
  /** Where to open when the whole drawing would be too small to read (the most connected table). */
  start?: Box | null;
  initial?: View | null;
};

export const Canvas = forwardRef<CanvasHandle, Props>(function Canvas(p, ref) {
  const box = useRef<HTMLDivElement>(null);
  const [view, setViewState] = useState<View>(() => p.initial ?? { x: 24, y: 24, k: 1 });
  const viewRef = useRef(view);
  const anim = useRef(0);
  const [space, setSpace] = useState(false);
  const pan = useRef<{ x: number; y: number; vx: number; vy: number; id: number; moved: boolean } | null>(null);
  const touches = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ d: number; k: number; cx: number; cy: number } | null>(null);
  const onView = useRef(p.onView);
  onView.current = p.onView;

  const size = () => {
    const el = box.current;
    const w = el?.clientWidth || FALLBACK.w, h = el?.clientHeight || FALLBACK.h;
    return { w, h };
  };
  const fitK = () => {
    const { w, h } = size();
    return Math.min(1, (w - 48) / Math.max(1, p.world.w), (h - 48) / Math.max(1, p.world.h));
  };
  const kMin = () => Math.max(0.05, Math.min(0.2, fitK() * 0.8));
  const clampK = (k: number) => Math.min(K_MAX, Math.max(kMin(), k));

  const setView = useCallback((v: View) => {
    viewRef.current = v;
    setViewState(v);
    onView.current?.(v);
  }, []);

  const tween = (to: View, animate = true) => {
    cancelAnimationFrame(anim.current);
    if (!animate || reduced()) {
      setView(to);
      return;
    }
    const from = viewRef.current, t0 = performance.now(), dur = 240;
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 3);
      setView({ x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, k: from.k + (to.k - from.k) * e });
      if (t < 1) anim.current = requestAnimationFrame(step);
    };
    anim.current = requestAnimationFrame(step);
  };

  const zoomAt = (k: number, sx: number, sy: number, animate = false) => {
    const v = viewRef.current, nk = clampK(k);
    const wx = (sx - v.x) / v.k, wy = (sy - v.y) / v.k;
    tween({ k: nk, x: sx - wx * nk, y: sy - wy * nk }, animate);
  };

  const fit = (animate = true) => {
    const { w, h } = size();
    const k = clampK(fitK());
    const x0 = p.world.x ?? 0, y0 = p.world.y ?? 0;
    tween({ k, x: (w - p.world.w * k) / 2 - x0 * k, y: Math.max(24, (h - p.world.h * k) / 2) - y0 * k }, animate);
  };

  const home = (animate = false) => {
    const s = p.start;
    if (!s || fitK() >= READABLE) return fit(animate);
    const { w, h } = size();
    const k = 0.8;
    const x0 = p.world.x ?? 0, y0 = p.world.y ?? 0;
    // the start box in the middle, but never a blank margin left of or above the drawing
    const x = Math.min(24 - x0 * k, w / 2 - (s.x + s.w / 2) * k);
    const y = Math.min(24 - y0 * k, h / 2 - (s.y + Math.min(s.h, h / k - 80) / 2) * k);
    tween({ k, x, y }, animate);
  };

  useImperativeHandle(ref, () => ({
    fit,
    home,
    zoomBy: (f: number) => {
      const { w, h } = size();
      zoomAt(viewRef.current.k * f, w / 2, h / 2, true);
    },
    zoomTo: (k: number) => {
      const { w, h } = size();
      zoomAt(k, w / 2, h / 2, true);
    },
    reveal: (b: Box, force = false) => {
      const { w, h } = size();
      const v = viewRef.current;
      const sx = b.x * v.k + v.x, sy = b.y * v.k + v.y;
      const inside = sx >= 8 && sy >= 8 && sx + b.w * v.k <= w - 8 && sy + Math.min(b.h, 200) * v.k <= h - 8;
      if (inside && !force && v.k >= 0.55) return;
      const k = v.k < 0.55 ? Math.min(1, clampK(0.9)) : v.k;
      tween({ k, x: w / 2 - (b.x + b.w / 2) * k, y: h / 2 - (b.y + Math.min(b.h, h / k - 80) / 2) * k });
    },
    view: () => viewRef.current,
    el: () => box.current,
  }));

  // first paint: fit the whole drawing (unless a view was handed in)
  const fitted = useRef(false);
  useLayoutEffect(() => {
    if (fitted.current) return;
    fitted.current = true;
    if (!p.initial) home(false);
    else onView.current?.(viewRef.current);
  });

  // React's onWheel is passive; zooming must stop the page from scrolling or zooming itself
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const v = viewRef.current;
      if (e.ctrlKey || e.metaKey) {
        zoomAt(v.k * Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0022)), e.clientX - r.left, e.clientY - r.top);
      } else {
        const dx = e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX, dy = e.shiftKey && !e.deltaX ? 0 : e.deltaY;
        const m = e.deltaMode === 1 ? 16 : 1;
        cancelAnimationFrame(anim.current);
        setView({ ...v, x: v.x - dx * m, y: v.y - dy * m });
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // Space held over the canvas: drag pans even over boxes
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.code !== "Space" || e.repeat || (t && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName))) return;
      if (!box.current?.matches(":hover") && !box.current?.contains(document.activeElement)) return;
      e.preventDefault();
      setSpace(true);
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpace(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const t = e.target as Element;
    if (t.closest(".dg-mini, .dg-overlay")) return;
    if (e.pointerType === "touch") {
      touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touches.current.size === 2) {
        const [a, b] = [...touches.current.values()];
        const r = box.current!.getBoundingClientRect();
        pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y), k: viewRef.current.k, cx: (a.x + b.x) / 2 - r.left, cy: (a.y + b.y) / 2 - r.top };
        pan.current = null;
        return;
      }
    }
    const onBox = !!t.closest("[data-tid]");
    if (p.grabbing || (onBox && !space && e.button !== 1)) return;
    if (e.button !== 0 && e.button !== 1) return;
    const v = viewRef.current;
    cancelAnimationFrame(anim.current);
    pan.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y, id: e.pointerId, moved: false };
    box.current?.setPointerCapture?.(e.pointerId);
    if (e.button === 1) e.preventDefault();
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "touch" && touches.current.has(e.pointerId)) {
      touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch.current && touches.current.size === 2) {
        const [a, b] = [...touches.current.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        zoomAt(pinch.current.k * (d / pinch.current.d), pinch.current.cx, pinch.current.cy);
        return;
      }
    }
    const d = pan.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!d.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
    d.moved = true;
    setView({ ...viewRef.current, x: d.vx + dx, y: d.vy + dy });
  };
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    touches.current.delete(e.pointerId);
    if (touches.current.size < 2) pinch.current = null;
    const d = pan.current;
    if (d && d.id === e.pointerId) {
      pan.current = null;
      const onBox = !!(e.target as Element).closest?.("[data-tid]");
      if (!d.moved && !onBox) p.onBackground?.();
    }
  };

  // the minimap: the whole drawing, the visible part as a frame; press or drag in it to move there
  const mini = useRef<HTMLDivElement>(null);
  const miniMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.type === "pointermove" && !(e.buttons & 1)) return;
    const el = mini.current!;
    const r = el.getBoundingClientRect();
    const W = p.world.w + 2 * MINI_PAD, H = p.world.h + 2 * MINI_PAD;
    const s = Math.min(r.width / W, r.height / H);
    const ox = (r.width - W * s) / 2, oy = (r.height - H * s) / 2;
    const wx = (e.clientX - r.left - ox) / s - MINI_PAD + (p.world.x ?? 0), wy = (e.clientY - r.top - oy) / s - MINI_PAD + (p.world.y ?? 0);
    const { w, h } = size();
    const v = viewRef.current;
    cancelAnimationFrame(anim.current);
    setView({ ...v, x: w / 2 - wx * v.k, y: h / 2 - wy * v.k });
    if (e.type === "pointerdown") el.setPointerCapture?.(e.pointerId);
  };
  const { w: cw, h: ch } = size();
  const vis = { x: -view.x / view.k, y: -view.y / view.k, w: cw / view.k, h: ch / view.k };
  const grid = 22 * view.k;

  return (
    <div ref={box} className={`dg-canvas ${space ? "space" : ""}`} tabIndex={0}
      role="application" aria-roledescription="diagram" aria-label={p.label} aria-describedby={p.describedBy}
      onKeyDown={p.onKeyDown} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{ backgroundSize: `${grid}px ${grid}px`, backgroundPosition: `${view.x}px ${view.y}px` }}>
      <svg className="dg-svg" width="100%" height="100%" aria-hidden="false">
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>{p.children}</g>
      </svg>
      {p.minimap && p.world.w > 0 && !(vis.x <= (p.world.x ?? 0) && vis.y <= (p.world.y ?? 0)
        && vis.x + vis.w >= (p.world.x ?? 0) + p.world.w && vis.y + vis.h >= (p.world.y ?? 0) + p.world.h) && (
        <div ref={mini} className="dg-mini" aria-hidden="true" onPointerDown={miniMove} onPointerMove={miniMove}>
          <svg viewBox={`${(p.world.x ?? 0) - MINI_PAD} ${(p.world.y ?? 0) - MINI_PAD} ${p.world.w + 2 * MINI_PAD} ${p.world.h + 2 * MINI_PAD}`} preserveAspectRatio="xMidYMid meet" width="100%" height="100%">
            {p.minimap}
            <rect className="dg-mini-view" x={vis.x} y={vis.y} width={vis.w} height={vis.h} />
          </svg>
        </div>
      )}
    </div>
  );
});

// keel's mascot: a little purple hull with two eyes and tiny feet, sitting by the bell. When a notification
// alerts it jumps (squash and stretch) and says the title in a speech bubble; a click on the bubble opens it.
// "Needs you" is a happy jump, a failure a worried shake. With prefers-reduced-motion it does not move, it only
// shows the bubble.

import { useEffect, useRef, useState } from "react";
import type { Notification as Note } from "../api";
import { NTONE } from "../notify";
import { useApp } from "../state";

const JUMP_MS = 900;
const BUBBLE_MS = 6000;
const REDUCED = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  try {
    return !!window.matchMedia?.(REDUCED)?.matches;
  } catch {
    return false;
  }
}

type Mood = "idle" | "happy" | "worried";
const moodOf = (n: Note): Mood => (n.type === "failed" ? "worried" : n.type === "review" ? "happy" : "idle");

export function MascotFigure({ mood = "idle", anim, k }: { mood?: Mood; anim?: "jump" | "shake" | null; k?: number }) {
  return (
    <svg key={k} className="mascot" viewBox="0 0 40 36" data-testid="mascot" data-mood={mood} data-anim={anim ?? undefined} aria-hidden="true">
      <g className="m-body">
        <ellipse className="m-foot" cx="13.5" cy="31.6" rx="4" ry="2.3" />
        <ellipse className="m-foot" cx="26.5" cy="31.6" rx="4" ry="2.3" />
        <path className="m-keel" d="M17.6 27.6 L20 33 L22.4 27.6 Z" />
        <path className="m-hull" d="M4.5 7 Q4.5 3 8.5 3 H31.5 Q35.5 3 35.5 7 C35.5 18.5 28.5 27.5 20 29.5 C11.5 27.5 4.5 18.5 4.5 7 Z" />
        <path className="m-deck" d="M8.5 3 H31.5 Q35.5 3 35.5 7 V7.6 H4.5 V7 Q4.5 3 8.5 3 Z" />
        <ellipse className="m-cheek" cx="10.6" cy="18" rx="2.2" ry="1.3" />
        <ellipse className="m-cheek" cx="29.4" cy="18" rx="2.2" ry="1.3" />
        <g className="m-eye">
          <ellipse className="m-white" cx="14.8" cy="13.4" rx="3.3" ry="3.7" />
          <circle className="m-pupil" cx="15.4" cy="14" r="1.8" />
          <circle className="m-shine" cx="16" cy="13.2" r=".6" />
        </g>
        <g className="m-eye">
          <ellipse className="m-white" cx="25.2" cy="13.4" rx="3.3" ry="3.7" />
          <circle className="m-pupil" cx="25.8" cy="14" r="1.8" />
          <circle className="m-shine" cx="26.4" cy="13.2" r=".6" />
        </g>
        <path className="m-brow" d="M11.8 9.4 L17 8.4 M28.2 9.4 L23 8.4" />
        <path className="m-mouth m-mouth-smile" d="M17.6 19.2 Q20 21.8 22.4 19.2" />
        <path className="m-mouth m-mouth-worried" d="M17.6 21.2 Q20 19 22.4 21.2" />
      </g>
    </svg>
  );
}

/** The mascot next to the bell; null when "Show keel" is off. */
export function Mascot() {
  const { alert, openNote, showMascot } = useApp();
  const [anim, setAnim] = useState<"jump" | "shake" | null>(null);
  const [mood, setMood] = useState<Mood>("idle");
  const [bubble, setBubble] = useState<Note | null>(null);
  // an alert from before this mascot mounted is not replayed
  const seen = useRef<number | null>(alert?.key ?? null);

  useEffect(() => {
    if (!alert || alert.key === seen.current) return;
    seen.current = alert.key;
    const m = moodOf(alert.note);
    setMood(m);
    setBubble(alert.note);
    setAnim(prefersReducedMotion() ? null : m === "worried" ? "shake" : "jump");
    const t1 = window.setTimeout(() => setAnim(null), JUMP_MS);
    const t2 = window.setTimeout(() => {
      setBubble(null);
      setMood("idle");
    }, BUBBLE_MS);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      seen.current = null; // a re-run (StrictMode) replays this alert instead of leaving it half done
    };
  }, [alert]);

  if (!showMascot) return null;
  return (
    <span className="mascot-wrap">
      <MascotFigure mood={mood} anim={anim} k={anim ? alert?.key : undefined} />
      {bubble && (
        <button type="button" className={`mascot-bubble t-${NTONE[bubble.type] ?? "run"}`} data-testid="mascot-bubble"
          aria-label={`Open notification: ${bubble.title}`}
          onClick={() => {
            openNote(bubble);
            setBubble(null);
            setMood("idle");
          }}>
          {bubble.title}
          <small>click to open</small>
        </button>
      )}
    </span>
  );
}

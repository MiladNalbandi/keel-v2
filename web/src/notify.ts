// Notification sound (WebAudio, no audio file), desktop notifications, and which notifications ring.
// Settings are stored by the api (/api/notification-settings) so every browser on this machine agrees.

import type { Notification as Note, NotificationSettings, NotificationType } from "./api";

export const NOTIFY_DEFAULTS: NotificationSettings = {
  sound: true,
  volume: 0.5,
  tone: "chime",
  popup: true,
  desktop: false,
  scope: "all",
  kinds: { review: true, failed: true, budget: true, finished: true, started: false },
  quiet: false,
};

export const EVTYPES: [NotificationType, string, string][] = [
  ["review", "Needs you", "a gate waits, a fix needs approval, a spec is ready"],
  ["failed", "Something failed", "a step, a ladder rung, an agent, a guard reverted an edit"],
  ["budget", "Budget", "a flow reaches 80% of its cap, or a cap stops it"],
  ["finished", "Finished", "a flow or a long agent step is done"],
  ["started", "Agent started", "every agent start — noisy, off by default"],
];

/** Pop-up / inbox colour per type (matches .pop.t-* and .note.t-*). */
export const NTONE: Record<NotificationType, "warn" | "bad" | "ok" | "run"> = {
  review: "warn",
  failed: "bad",
  budget: "warn",
  finished: "ok",
  started: "run",
};

export function withDefaults(s: Partial<NotificationSettings> | null | undefined): NotificationSettings {
  return { ...NOTIFY_DEFAULTS, ...(s ?? {}), kinds: { ...NOTIFY_DEFAULTS.kinds, ...(s?.kinds ?? {}) } };
}

/** Should this notification make noise (sound, pop-up, desktop)? It always goes to the inbox. */
export function shouldAlert(n: Note, s: NotificationSettings, currentProject: string | null): boolean {
  if (!s.kinds[n.type]) return false;
  if (s.scope === "project" && currentProject && n.project_id !== currentProject) return false;
  if (s.quiet) return false;
  return true;
}

// ---------- sound ----------

let audio: AudioContext | null = null;

function ctx(): AudioContext | null {
  try {
    const AC: typeof AudioContext | undefined =
      typeof window !== "undefined"
        ? window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        : undefined;
    if (!AC) return null;
    audio = audio ?? new AC();
    if (audio.state === "suspended") void audio.resume();
    return audio;
  } catch {
    return null;
  }
}

/** Browsers only allow sound after a click; prepare the audio context on the first one. */
export function unlockAudio() {
  const make = () => ctx();
  window.addEventListener("pointerdown", make, { once: true });
  window.addEventListener("keydown", make, { once: true });
}

/** Test hook: forget the cached AudioContext. */
export function resetAudio() {
  audio = null;
}

/** Chime: two rising notes. Soft: one note. A failure is always two lower, falling notes. */
export function playSound(type: NotificationType, s: Pick<NotificationSettings, "sound" | "tone" | "volume">): boolean {
  if (!s.sound || s.tone === "off") return false;
  const a = ctx();
  if (!a) return false;
  try {
    const notes = type === "failed" ? [392, 262] : s.tone === "soft" ? [523] : [660, 880];
    notes.forEach((hz, i) => {
      const o = a.createOscillator();
      const g = a.createGain();
      const t0 = a.currentTime + i * 0.16;
      o.type = s.tone === "soft" ? "sine" : "triangle";
      o.frequency.value = hz;
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(0.25 * s.volume, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.35);
      o.connect(g);
      g.connect(a.destination);
      o.start(t0);
      o.stop(t0 + 0.4);
    });
    return true;
  } catch {
    return false;
  }
}

/** v0.15.2 KeelBot's own sound when an answer is ready: three quick, soft rising notes, each sliding up a little
 *  (sine). It is not the chime (two notes), the soft tone (one note) or the failure tone (two falling notes). */
export const KEELBOT_NOTES = [587, 784, 1175];
export function playKeelBot(volume: number): boolean {
  const a = ctx();
  if (!a) return false;
  try {
    KEELBOT_NOTES.forEach((hz, i) => {
      const o = a.createOscillator();
      const g = a.createGain();
      const t0 = a.currentTime + i * 0.085;
      o.type = "sine";
      o.frequency.value = hz;
      o.frequency.setValueAtTime?.(hz * 0.94, t0);
      o.frequency.exponentialRampToValueAtTime?.(hz, t0 + 0.05);
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(0.22 * volume, t0 + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.24);
      o.connect(g);
      g.connect(a.destination);
      o.start(t0);
      o.stop(t0 + 0.26);
    });
    return true;
  } catch {
    return false;
  }
}

// ---------- desktop ----------

export const canNotify = () => typeof window !== "undefined" && "Notification" in window;
export const permission = (): NotificationPermission | "unsupported" =>
  canNotify() ? window.Notification.permission : "unsupported";

export async function askPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!canNotify()) return "unsupported";
  try {
    return await Promise.resolve(window.Notification.requestPermission());
  } catch {
    return window.Notification.permission;
  }
}

export function desktopPop(n: Note, projectName: string, onClick: () => void) {
  if (!canNotify() || window.Notification.permission !== "granted") return;
  try {
    const d = new window.Notification(`keel · ${n.title}`, { body: `${projectName || n.project_id} · ${n.body}`, tag: `keel-${n.id}` });
    d.onclick = () => {
      window.focus();
      onClick();
      d.close();
    };
  } catch {
    /* some browsers allow notifications only from a service worker */
  }
}

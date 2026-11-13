// The one right-side panel that walks the person through a sign-in. keel 0.18.57: four numbered steps, one at a time —
// 1 Start the login · 2 Open the page (and sign in) · 3 Paste the code from that page, or Type the code on that page (a
// device code) · 4 Saved — and a second way, "Paste a token". Settings › Connections › GitHub's "Sign in with GitHub"
// uses it (#101 the AI providers' CLI logins are gone: they take an API key); a sign-in differs only in an adapter
// (start / poll / cancel) and a few small slots, so the steps, the copy buttons and the error handling exist once.
//
// A refused code (an OAuth 400: a code from an older tab) does not end the login: the server answers with the
// error and a NEW link. The code step turns red with the error and "Start again", which goes back to step 2 with the
// new link; the next code is sent to the same login. A login that fails or is cancelled says why at its step, and
// "Start again" starts a new one.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorParts } from "../api";
import { copyText } from "../clipboard";
import { useApp } from "../state";
import { Note, Seg, Steps, type StepItem } from "./settingsUi";
import { Drawer } from "./ui";

/** What every login answers with, whatever endpoint it comes from. */
/** error: why the last step was refused (a Claude code); the login still runs, the step shows it in red. */
export type LoginBase = {
  id: string;
  status: string;
  url?: string | null;
  code?: string | null;
  message: string;
  error?: string | null;
};

export type LoginAdapter<V extends LoginBase> = {
  start: () => Promise<V>;
  poll: (id: string) => Promise<V>;
  cancel: (id: string) => Promise<unknown>;
};

/** Handed to the code step so it can change the login or show an error in the drawer. `sent` says a code went out
 *  (a refusal after it shows again, even with the same words). */
export type LoginCtx<V extends LoginBase> = {
  login: V | null;
  setLogin: (v: V) => void;
  setErr: (m: string | null) => void;
  sent: () => void;
};

const TERMINAL = ["done", "failed", "cancelled"];
const ENDED = ["failed", "cancelled"];

function copyToast(text: string, toast: (m: string) => void) {
  void copyText(text).then((ok) =>
    toast(ok ? "Copied." : "Select the text to copy it."),
  );
}

const hostOf = (url?: string | null) => {
  try {
    return url ? new URL(url).host : "";
  } catch {
    return "";
  }
};

export function LoginDrawer<V extends LoginBase>({
  title,
  intro,
  note,
  adapter,
  doneText,
  onDone,
  onClose,
  before,
  openTitle,
  codeStep,
  titles,
  status,
  paste,
}: {
  title: string;
  intro: string;
  /** A hint under the intro (for example "authorize your organisation"). */
  note?: ReactNode;
  adapter: LoginAdapter<V>;
  /** The line shown when the login is saved. */
  doneText: (v: V) => string;
  /** Runs once when the login is saved. */
  onDone: (v: V) => void;
  onClose: () => void;
  /** Fields shown before the login starts (the GitHub host). */
  before?: (login: V | null) => ReactNode;
  /** Step 2's title, for example "Open claude.com and sign in with your Claude account". */
  openTitle?: string;
  /** A login that asks for a code the page shows (Claude): step 3's field. Without it step 3 shows the device code. */
  codeStep?: (ctx: LoginCtx<V>) => ReactNode;
  /** The four titles, when the place says them its own way (GitHub). */
  titles?: [string, string, string, string];
  /** Shown under the saved line (the connection test). */
  status?: ReactNode;
  /** The second way, "Paste a token": it calls `saved` with the line to show once the token is saved. */
  paste?: (saved: (text: string) => void) => ReactNode;
}) {
  const { toast } = useApp();
  const [way, setWay] = useState<"here" | "paste">("here");
  const [login, setLogin] = useState<V | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // the person opened (or copied) the link this login shows: the next step is the code
  const [opened, setOpened] = useState(false);
  // a refused code the person has seen and answered with "Start again"; null after a new code goes out
  const [acked, setAcked] = useState<string | null>(null);
  // a token saved with "Paste a token"
  const [pasted, setPasted] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(
    () => () => {
      live.current = false;
    },
    [],
  );

  // poll while the login runs; one failed poll is only skipped: the next one is armed again (same interval)
  const [again, setAgain] = useState(0);
  useEffect(() => {
    if (!login || TERMINAL.includes(login.status)) return;
    const t = window.setTimeout(() => {
      adapter.poll(login.id).then(
        (v) => live.current && setLogin(v),
        () => live.current && setAgain((n) => n + 1),
      );
    }, 2000);
    return () => window.clearTimeout(t);
  }, [login, again]);

  // when it is saved
  useEffect(() => {
    if (login?.status === "done") onDone(login);
  }, [login?.status]);

  const start = async () => {
    setBusy(true);
    setErr(null);
    setOpened(false);
    setAcked(null);
    try {
      setLogin(await adapter.start());
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  const close = () => {
    if (login && !TERMINAL.includes(login.status))
      void adapter.cancel(login.id).catch(() => undefined);
    onClose();
  };

  const done = login?.status === "done" || !!pasted;
  const ended = !!login && ENDED.includes(login.status);
  const refused =
    !!login?.error &&
    login.error !== acked &&
    !ENDED.includes(login.status) &&
    !done;
  const host = hostOf(login?.url);
  // where the person is: 0 start · 1 open the page · 2 the code · 3 saved
  const at = done ? 3 : !login ? 0 : refused ? 2 : !login.url || opened ? 2 : 1;
  const failedAt = done ? -1 : !login && err ? 0 : refused || ended ? at : -1;
  const words = titles ?? [
    "Start the login",
    openTitle ?? (host ? `Open this page: ${host}` : "Open this page"),
    codeStep ? "Paste the code from that page" : "Type the code on that page",
    "Saved",
  ];
  const restart = () => {
    if (refused && login) {
      // the same login waits for a new code: back to the (new) link
      setAcked(login.error ?? null);
      setOpened(false);
    } else void start();
  };
  const failBox = (text: string) => (
    <div
      className="ss-note t-err"
      role="alert"
      style={{ display: "flex", flexDirection: "column", gap: 6 }}
    >
      <b>✕ {text}</b>
      <button
        className="ss-btn sm d"
        type="button"
        style={{ alignSelf: "flex-start" }}
        onClick={restart}
        disabled={busy}
      >
        Start again
      </button>
    </div>
  );

  // the current step's first button or field takes the focus when the step changes (a screen reader follows it)
  const box = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    box.current
      ?.querySelector<HTMLElement>(
        '[aria-current="step"] input, [aria-current="step"] button, [aria-current="step"] a',
      )
      ?.focus();
  }, [at, failedAt, way]);

  const steps: StepItem[] = words.map((title, i) => {
    const state: StepItem["state"] =
      i === failedAt
        ? "failed"
        : i < at || (i === 3 && done)
          ? "done"
          : i === at
            ? "now"
            : "todo";
    let body: ReactNode = null;
    if (i === 0 && at === 0)
      body = (
        <>
          {before?.(login)}
          <button
            className="ss-btn p"
            type="button"
            onClick={start}
            disabled={busy}
            style={{ alignSelf: "flex-start" }}
          >
            {busy ? "Starting…" : err ? "Start again" : "Start login"}
          </button>
          {err && (
            <Note tone="err" role="alert">
              ✕ {err}
            </Note>
          )}
        </>
      );
    if (i === 1 && at === 1 && login?.url)
      body = (
        <>
          <div className="ss-actions">
            <a
              className="ss-btn p"
              href={login.url}
              target="_blank"
              rel="noreferrer"
              onClick={() => setOpened(true)}
            >
              Open {host} <span aria-hidden="true">↗</span>
            </a>
            <button
              className="ss-btn"
              type="button"
              onClick={() => {
                copyToast(login.url!, toast);
                setOpened(true);
              }}
            >
              Copy link
            </button>
          </div>
          {ended && failBox(login.message)}
        </>
      );
    if (i === 2 && at === 2 && login)
      body = (
        <>
          {refused || ended ? null : codeStep ? (
            codeStep({ login, setLogin, setErr, sent: () => setAcked(null) })
          ) : login.code ? (
            <>
              <p className="ss-sub">Type this code on that page:</p>
              <div className="ss-actions">
                <b className="ss-code">{login.code}</b>
                <button
                  className="ss-btn"
                  type="button"
                  onClick={() => copyToast(login.code!, toast)}
                >
                  Copy code
                </button>
              </div>
            </>
          ) : null}
          {!refused && !ended && (
            <p className="ss-sub" role="status">
              <span
                className="ss-swatch"
                style={{
                  background: "var(--run)",
                  borderRadius: 4,
                  marginRight: 8,
                }}
              />
              … {login.message}
            </p>
          )}
          {login.url && !refused && !ended && (
            <span className="ss-meta ss-small">
              The page did not open?{" "}
              <a href={login.url} target="_blank" rel="noreferrer">
                Open {host} again
              </a>{" "}
              ·{" "}
              <button
                type="button"
                className="ss-btn link"
                onClick={() => copyToast(login.url!, toast)}
              >
                Copy link
              </button>
            </span>
          )}
          {(refused || ended) &&
            failBox(refused ? login.error! : login.message)}
        </>
      );
    if (i === 3 && done)
      body = (
        <>
          <p className="ss-note t-ok" role="status">
            ✓ {pasted ?? doneText(login!)}
          </p>
          {status}
        </>
      );
    return { title, state, body };
  });

  return (
    <Drawer
      title={title}
      onClose={close}
      className="ss-drawer"
      footer={
        <button className="ss-btn" type="button" onClick={close}>
          Close
        </button>
      }
    >
      <div
        ref={box}
        style={{ display: "flex", flexDirection: "column", gap: 20 }}
      >
        {paste && (
          <Seg
            label="How to log in"
            className="ss-way"
            value={way}
            onChange={setWay}
            options={[
              ["here", "Log in here"],
              ["paste", "Paste a token"],
            ]}
          />
        )}
        {way === "here" || !paste ? (
          <>
            <p className="ss-sub">{intro}</p>
            {note && <p className="ss-sub">{note}</p>}
            <Steps steps={steps} />
          </>
        ) : (
          paste((text) => {
            setPasted(text);
            setWay("here");
          })
        )}
      </div>
    </Drawer>
  );
}

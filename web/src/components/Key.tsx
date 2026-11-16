// A key drawn as a key on a button ("Approve A"). Hidden from screen readers: the button says its key with
// aria-keyshortcuts, and its name stays the words only.
export function Key({ k }: { k: string }) {
  return <kbd aria-hidden="true">{k}</kbd>;
}

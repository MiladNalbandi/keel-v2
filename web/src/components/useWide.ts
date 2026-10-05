// Wide screens (1100 px and more) show side panels next to the main column; narrow ones stack them or use drawers.

import { useEffect, useState } from "react";

export function useWide() {
  const q = "(max-width: 1099px)";
  const [narrow, setNarrow] = useState(() => !!window.matchMedia?.(q).matches);
  useEffect(() => {
    const m = window.matchMedia?.(q);
    if (!m) return;
    const on = () => setNarrow(m.matches);
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, []);
  return !narrow;
}

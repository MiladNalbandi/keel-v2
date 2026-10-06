// An empty state that says what to do: a title, one line, and the action that fills the screen.

import { forwardRef, type ReactNode } from "react";

export const EmptyState = forwardRef<HTMLDivElement, {
  title: ReactNode; children?: ReactNode; action?: ReactNode; className?: string; testId?: string;
}>(function EmptyState({ title, children, action, className = "", testId }, ref) {
  return (
    <div className={`empty-state ${className}`} data-testid={testId} ref={ref} tabIndex={-1}>
      <b className="es-title">{title}</b>
      {children && <span className="es-line">{children}</span>}
      {action && <div className="es-act">{action}</div>}
    </div>
  );
});

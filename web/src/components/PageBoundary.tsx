// One broken screen must not turn the whole app black: show what happened, keep the sidebar working.
import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode; resetKey: string };
type State = { error: Error | null };

export class PageBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("A page crashed:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });   // another page or project: try again
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="errbox" role="alert" style={{ margin: 16 }}>
        <b>This page could not be shown.</b>
        <p className="sub" style={{ margin: "6px 0" }}>It is a bug in keel, not something you did. The rest of keel still works: use the menu to go somewhere else.</p>
        <p className="mono sub" style={{ margin: "6px 0", overflowWrap: "anywhere" }}>{this.state.error.message.slice(0, 300)}</p>
        <button className="btn" type="button" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}

import { Component, ReactNode, useEffect, useState } from "react";

// One page crashing must not blank the whole app: show what broke and keep the sidebar working.
class PageBoundary extends Component<{ children: ReactNode; route: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidUpdate(prev: { route: string }) {
    if (prev.route !== this.props.route && this.state.error) this.setState({ error: null });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <div className="banner bad">
          <div className="spacer small">
            <b>This page hit an error.</b> {this.state.error.message}
            <div className="muted" style={{ marginTop: 4 }}>Other pages still work. Reload to try again.</div>
          </div>
          <button onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}
import { api } from "./api";
import Applications from "./pages/Applications";
import Home from "./pages/Home";
import Jobs from "./pages/Jobs";
import Messages from "./pages/Messages";
import Profile from "./pages/Profile";
import ReachOut from "./pages/ReachOut";
import Settings from "./pages/Settings";
import { Icon, ToastHost, usePoll } from "./ui";

const PAGES: [string, string, string][] = [
  ["home", "Home", "home"],
  ["jobs", "Jobs", "jobs"],
  ["applications", "Applications", "apps"],
  ["reach", "Reach out", "reach"],
  ["messages", "Messages", "msg"],
  ["profile", "Profile", "user"],
  ["settings", "Settings", "gear"],
];

function useRoute(): [string, (r: string) => void] {
  const get = () => (window.location.hash.replace(/^#\/?/, "").split("?")[0] || "home");
  const [r, setR] = useState(get());
  useEffect(() => {
    const on = () => { setR(get()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [r, (x: string) => { window.location.hash = `/${x}`; }];
}

export default function App() {
  const [route, go] = useRoute();
  const [state] = usePoll(() => api("/api/state"), 6000);
  const s = state?.stats;
  const ap = state?.autopilot;
  const Page = { home: Home, jobs: Jobs, applications: Applications, reach: ReachOut, messages: Messages, profile: Profile, settings: Settings }[route] || Home;
  return (
    <ToastHost>
      <div className="shell">
        <aside className="side">
          <div className="brand">
            <span className="logo"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="m5 13 4 4L19 7" /></svg></span>
            AutoApply
          </div>
          <nav className="nav col" style={{ gap: 2 }}>
            {PAGES.map(([id, label, icon]) => (
              <a key={id} href={`#/${id}`} className={route === id ? "on" : ""} onClick={() => go(id)}>
                <Icon name={icon} />{label}
                {id === "applications" && s?.needs_you ? <span className="count">{s.needs_you}</span> : null}
              </a>
            ))}
          </nav>
          <div className="side-foot">
            {s && ap && (
              <>
                <div className="tiny muted"><b style={{ color: s.sent_today >= ap.daily_cap ? "var(--bad)" : "var(--text)" }}>{s.sent_today}</b> of {ap.daily_cap} sent today</div>
                <div className="row tiny" style={{ color: ap.enabled ? "var(--good)" : "var(--muted)" }}>
                  <span className={`dot ${ap.enabled ? "on" : ""}`} />
                  {ap.enabled ? (ap.phase === "idle" ? "Autopilot on" : ap.detail || ap.phase) : "Autopilot off"}
                </div>
              </>
            )}
          </div>
        </aside>
        <main className="main">
          <PageBoundary route={route}><Page state={state} go={go} /></PageBoundary>
        </main>
      </div>
    </ToastHost>
  );
}

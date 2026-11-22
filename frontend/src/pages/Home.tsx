import { useState } from "react";
import { ago, api, until } from "../api";
import { AppList } from "./Applications";
import { Icon, Toggle, useToast } from "../ui";

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Home({ state, go }: { state: any; go: (r: string) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!state) return <div className="page"><div className="empty">Loading…</div></div>;
  const { stats: s, autopilot: ap } = state;
  const capHit = s.sent_today >= ap.daily_cap;
  const active = ap.phase && ap.phase !== "idle";

  const setAuto = async (on: boolean) => {
    try {
      await api("/api/autopilot", { body: { enabled: on } });
      toast(on ? "Autopilot is on" : "Autopilot is off");
    } catch (e: any) { toast(e.message); }
  };
  const runNow = async () => {
    setBusy(true);
    try { await api("/api/run-now", { body: {} }); toast("Looking for new jobs now"); }
    catch (e: any) { toast(e.message); }
    finally { setTimeout(() => setBusy(false), 2500); }
  };

  const statusLine = ap.enabled
    ? `Autopilot is on. ${s.sent_today ? `Sent ${s.sent_today} today, ` : ""}${s.sent_7d} this week.`
    : `Autopilot is off. ${s.sent_7d} sent this week.`;

  return (
    <div className="page">
      <div className="hero">
        <h1>{greeting()}{state.name ? `, ${state.name}` : ""}.</h1>
        <p>{statusLine}</p>
      </div>

      {state.missing.length > 0 && (
        <div className="banner warn">
          <Icon name="alert" />
          <div className="spacer">
            <div><b>Finish your profile to start applying.</b></div>
            <div className="small">Missing: {state.missing.join(", ")}</div>
          </div>
          <button onClick={() => go("profile")}>Open profile</button>
        </div>
      )}
      {!state.llm && (
        <div className="banner bad">
          <Icon name="alert" />
          <div className="spacer small">
            <b>No Claude API key.</b> Add <code>ANTHROPIC_API_KEY=…</code> to the <code>.env</code> file and restart. Without it, standard fields still fill, but written answers, resume reading and job scoring are off.
          </div>
        </div>
      )}
      {capHit && ap.enabled && (
        <div className="banner info">
          <Icon name="clock" />
          <div className="spacer small">Today's limit of {ap.daily_cap} is reached. Prepared applications go out tomorrow. Change the limit in Settings.</div>
        </div>
      )}

      <div className="card">
        <div className="row small muted" style={{ marginBottom: 6 }}>
          {active ? <span className="spin" /> : <span className={`dot ${ap.enabled ? "on" : ""}`} />}
          {active ? ap.detail || ap.phase : ap.enabled ? `Idle · next search ${until(ap.next_cycle)}` : "Paused"}
          {s.applying + s.queued > 0 && <span>· {s.applying} applying, {s.queued} queued</span>}
        </div>
        <div className="row" style={{ marginBottom: 4 }}>
          <h2 style={{ margin: 0 }}>Auto-apply is {ap.enabled ? "on" : "off"}</h2>
          <span className="spacer" />
          <Toggle on={ap.enabled} onChange={setAuto} label="Autopilot" />
        </div>
        <p className="small muted">
          Every {ap.interval_minutes >= 60 ? `${ap.interval_minutes / 60 === 1 ? "hour" : `${ap.interval_minutes / 60} hours`}` : `${ap.interval_minutes} minutes`} it finds new jobs, fills them in and{" "}
          {ap.submit_mode === "review" ? "waits for your OK before sending" : "sends them"}. Never more than {ap.per_company ?? 3} to one company a month, days apart.
          {state.last_sent && <> Last one sent {ago(state.last_sent)}.</>}
        </p>
        {ap.last_error && <p className="small" style={{ color: "var(--bad)", marginTop: 6 }}>Last error: {ap.last_error}</p>}
        <div style={{ marginTop: 14 }}>
          <button className="primary big" disabled={busy || state.missing.length > 0} onClick={runNow}>
            <Icon name="play" size={14} />Apply to new jobs now
          </button>
        </div>
      </div>

      <div className="small muted col" style={{ gap: 4 }}>
        {state.gmail.configured && state.gmail.ok === false
          ? <span className="row" style={{ color: "var(--bad)", alignItems: "flex-start" }}><Icon name="alert" size={14} /><span><b>Gmail isn't working.</b> {state.gmail.error} <a href="#/settings">Fix it in Settings</a></span></span>
          : state.gmail.configured
          ? <span className="row" style={{ color: "var(--good)" }}><Icon name="check" size={14} />Gmail {state.gmail.ok ? "connected" : "saved, checking soon"} ({state.gmail.address}). Codes and replies are read automatically.</span>
          : <span className="row"><Icon name="mail" size={14} />Gmail not connected. <a href="#/settings">Connect it</a> so verification codes and employer replies are handled for you.</span>}
        {state.gaps.length > 0 && <span>Optional, for better answers, add to <a href="#/profile">your profile</a>: {state.gaps.join("; ")}.</span>}
      </div>

      <div className="card flush">
        <div className="stats">
          <div className="stat"><div className="k">Applications sent</div><div className="v">{s.sent_total}</div></div>
          <div className="stat"><div className="k">Sent today</div><div className="v">{s.sent_today}</div></div>
          <div className="stat"><div className="k">Interviews & assessments</div><div className="v">{s.interviews}</div></div>
          <div className="stat"><div className="k">Employer replies, 30 days</div><div className="v">{s.replies_30d}</div></div>
        </div>
      </div>

      {state.spend && (
        <div className="small muted row" style={{ marginTop: -6 }}>
          Claude cost: <b style={{ color: "var(--text)" }}>${state.spend.today.toFixed(2)}</b> today ·{" "}
          <b style={{ color: "var(--text)" }}>${state.spend.month.toFixed(2)}</b> this month
          <span className="faint">· {({ quality: "Best quality", balanced: "Balanced", saver: "Saver" } as Record<string, string>)[state.spend.mode] || state.spend.mode} mode</span>
          <a href="#/settings" className="small" style={{ marginLeft: "auto" }}>Change</a>
        </div>
      )}

      <div className="row"><h2 style={{ margin: 0 }}>Applications</h2><span className="spacer" /><a href="#/applications" className="small">See all</a></div>
      <div className="card flush">
        {state.recent.length ? <AppList rows={state.recent} /> : <div className="empty">Nothing yet. Turn on autopilot or press “Apply to new jobs now”.</div>}
      </div>

      {state.events.length > 0 && (
        <>
          <h2 style={{ margin: "4px 0 0" }}>Activity</h2>
          <div className="card col small" style={{ gap: 6 }}>
            {state.events.map((e: any, i: number) => (
              <div key={i} className="row"><span className="faint" style={{ width: 70, flexShrink: 0 }}>{ago(e.t)}</span><span>{e.msg}</span></div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

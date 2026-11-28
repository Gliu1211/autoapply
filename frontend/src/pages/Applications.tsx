import { useState } from "react";
import { ago, Answer, api, AppRow, Pending } from "../api";
import { Icon, RESPONSE_LABEL, Seg, StatusLine, usePoll, useToast } from "../ui";

const SOURCE_LABEL: Record<string, string> = { profile: "from profile", remembered: "remembered", generated: "written by Claude", rule: "", you: "your answer", prefilled: "already filled in", resume: "left to your resume" };

function Meta({ a }: { a: AppRow }) {
  const bits = [
    a.ats || a.source,
    a.questions_answered ? `${a.questions_answered} question${a.questions_answered === 1 ? "" : "s"} answered` : "",
    a.used_resume ? "your uploaded resume" : "",
  ].filter(Boolean);
  return (
    <div className="tiny faint">
      {bits.join(" · ")}
      {a.blank_required > 0 && <span style={{ color: "var(--warn)" }}> · {a.blank_required} required left blank</span>}
      {a.status !== "sent" && a.started_at && <> · started {ago(a.started_at)}</>}
      {a.posted_at && <> · posted {ago(a.posted_at)}</>}
    </div>
  );
}

function PendingForm({ app, pending, onDone }: { app: number; pending: Pending[]; onDone: () => void }) {
  const toast = useToast();
  const [vals, setVals] = useState<Record<string, string>>({});
  const save = async () => {
    try {
      await api(`/api/applications/${app}/answers`, { body: { answers: vals } });
      toast("Saved. These answers are remembered for future forms. Retrying now.");
      onDone();
    } catch (e: any) { toast(e.message); }
  };
  return (
    <div className="entry" style={{ borderColor: "var(--warn)" }}>
      <div className="small"><b>Answer these once</b> <span className="muted">and they'll be reused on every future form. Or finish it yourself in the browser window: what you put in these boxes there is remembered too.</span></div>
      {pending.map((p) => (
        <label className="f" key={p.question}>
          <span className="l">{p.question}</span>
          {p.options?.length ? (
            <select value={vals[p.question] || ""} onChange={(e) => setVals({ ...vals, [p.question]: e.target.value })}>
              <option value="">Select…</option>
              {p.options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : p.kind === "file" ? (
            <span className="hint">This form wants a file the profile doesn't have. Use “Open in browser” to attach it yourself.</span>
          ) : (
            <textarea rows={2} value={vals[p.question] || ""} onChange={(e) => setVals({ ...vals, [p.question]: e.target.value })} />
          )}
        </label>
      ))}
      <div><button className="primary" onClick={save}>Save and retry</button></div>
    </div>
  );
}

function Detail({ id, onChange }: { id: number; onChange: () => void }) {
  const toast = useToast();
  const [d, refresh] = usePoll(() => api(`/api/applications/${id}`), 5000, [id]);
  const [zoom, setZoom] = useState<string | null>(null);
  if (!d) return <div className="item-body small muted">Loading…</div>;
  const act = async (path: string, msg: string) => {
    try { await api(`/api/applications/${id}/${path}`, { body: {} }); toast(msg); refresh(); onChange(); }
    catch (e: any) { toast(e.message); }
  };
  const answers: Answer[] = (d.answers || []).filter((x: Answer) => x.answer || x.required);
  const shots: string[] = d.screenshots || [];
  return (
    <div className="item-body">
      <div className="row wrap">
        {d.status === "ready" && <button className="primary" onClick={() => act("approve", "Approved. Sending now.")}><Icon name="check" size={14} />Approve and send</button>}
        {["needs_you", "failed", "skipped", "ready"].includes(d.status) && <button onClick={() => act("retry", "Queued again")}>Retry</button>}
        {d.status === "queued" && <button onClick={() => act("retry", "Moved up")}>Send next</button>}
        <button onClick={() => act("open", "Opened in the browser window")}><Icon name="ext" size={14} />Open in browser</button>
        {d.status !== "sent" && <button onClick={() => act("mark-sent", "Marked as sent")}>I sent it myself</button>}
        {!["sent", "skipped"].includes(d.status) && <button className="ghost danger" onClick={() => act("skip", "Skipped")}>Skip</button>}
        <span className="spacer" />
        {d.job?.url && <a className="small" href={d.job.url} target="_blank" rel="noreferrer">Job posting ↗</a>}
      </div>

      {d.pending_questions?.length > 0 && d.status === "needs_you" && <PendingForm app={id} pending={d.pending_questions} onDone={() => { refresh(); onChange(); }} />}

      {d.job?.score != null && <div className="small muted">Match {d.job.score}% · {d.job.score_reason}{d.job.salary ? ` · ${d.job.salary}` : ""}</div>}

      {answers.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 8 }}>What went in the boxes</h3>
          <div className="qa">
            {answers.map((x, i) => (
              <div key={i} style={{ display: "contents" }}>
                <div className="q">{x.question}{x.required && <span style={{ color: "var(--bad)" }}> *</span>}</div>
                <div className="a">
                  {x.answer ? x.answer : <span style={{ color: "var(--warn)" }}>left blank</span>}
                  {x.answer && !x.ok && <span className="badge warn" style={{ marginLeft: 6 }}>didn't stick</span>}
                  {x.answer && SOURCE_LABEL[x.source] && <span className="tiny faint"> · {SOURCE_LABEL[x.source]}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {d.cover_letter && (
        <details>
          <summary className="small" style={{ cursor: "pointer" }}>Cover letter</summary>
          <p className="small" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{d.cover_letter}</p>
        </details>
      )}

      {shots.length > 0 && (
        <div className="row wrap" style={{ alignItems: "flex-start" }}>
          {shots.slice(-3).map((s) => <img key={s} className="shot" src={`/shots/${s}`} alt={s} onClick={() => setZoom(s)} />)}
        </div>
      )}

      {d.messages?.length > 0 && (
        <div className="col" style={{ gap: 4 }}>
          <h3>Emails from {d.job?.company}</h3>
          {d.messages.map((m: any) => (
            <div key={m.id} className="small row"><span className="badge">{RESPONSE_LABEL[m.category]?.[0] || m.category}</span>{m.summary || m.subject}<span className="faint">· {ago(m.received_at)}</span></div>
          ))}
        </div>
      )}

      {d.log?.length > 0 && <div className="log">{d.log.slice(-12).map((l: any, i: number) => <div key={i}>{new Date(l.t).toLocaleTimeString()} {l.msg}</div>)}</div>}
      {zoom && <div className="modal" onClick={() => setZoom(null)}><img src={`/shots/${zoom}`} alt="" /></div>}
    </div>
  );
}

export function AppList({ rows, onChange }: { rows: AppRow[]; onChange?: () => void }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="list">
      {rows.map((a) => (
        <div className="item" key={a.id}>
          <div className="item-h" onClick={() => setOpen(open === a.id ? null : a.id)}>
            <span className={`chev ${open === a.id ? "open" : ""}`}><Icon name="chev" size={14} /></span>
            <div className="col" style={{ gap: 2, minWidth: 0, flex: 1 }}>
              <div className="row wrap" style={{ gap: 8 }}>
                <span className="company">{a.company}</span>
                <span className="small muted" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{a.title}</span>
                {a.score != null && <span className={`match ${a.score >= 65 ? "hi" : ""}`}>{a.score}% match</span>}
                {a.response && RESPONSE_LABEL[a.response] && <span className={`badge ${RESPONSE_LABEL[a.response][1]}`}>{RESPONSE_LABEL[a.response][0]}</span>}
              </div>
              <StatusLine status={a.status} detail={a.status_detail} sentAt={a.sent_at} updatedAt={a.updated_at} />
              <Meta a={a} />
            </div>
          </div>
          {open === a.id && <Detail id={a.id} onChange={onChange || (() => {})} />}
        </div>
      ))}
    </div>
  );
}

type Tab = "everything" | "needs_you" | "not_sent" | "sent";

export default function Applications({ state }: { state: any }) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("everything");
  const [q, setQ] = useState("");
  const [days, setDays] = useState("0");
  const [resp, setResp] = useState("");
  const [link, setLink] = useState("");
  const [rows, refresh] = usePoll<AppRow[]>(
    () => api(`/api/applications?tab=${tab}&q=${encodeURIComponent(q)}&days=${days}&response=${resp}`), 5000, [tab, q, days, resp]);
  const s = state?.stats;

  const addLink = async () => {
    try {
      await api("/api/jobs/add", { body: { url: link } });
      setLink("");
      toast("Added. It's next in line.");
      refresh();
    } catch (e: any) { toast(e.message); }
  };

  const waiting = (rows || []).filter((r) => r.status !== "sent");
  const sent = (rows || []).filter((r) => r.status === "sent");

  return (
    <div className="page">
      <div>
        <h1>Applications</h1>
        <p className="muted small">Everything AutoApply applied to. Open one to see every question the employer asked and exactly what went in the box, or, if it didn't go out, what stopped it.{s?.not_sent ? ` ${s.not_sent} of these haven't gone out yet.` : ""}</p>
      </div>
      {s && (
        <div className="card flush">
          <div className="stats">
            <div className="stat"><div className="v">{s.sent_total}</div><div className="small muted">sent, all time</div></div>
            <div className="stat"><div className="v">{s.sent_today}</div><div className="small muted">sent today</div></div>
            <div className="stat"><div className="v">{s.sent_7d}</div><div className="small muted">sent in 7 days</div></div>
            <div className="stat"><div className="v">{s.emailed_back}</div><div className="small muted">employers emailed back</div></div>
          </div>
        </div>
      )}
      <div className="row">
        <input placeholder="Paste any job link to apply to it next (Greenhouse, Lever, Ashby, Workday, Handshake, …)" value={link} onChange={(e) => setLink(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addLink()} />
        <button className="primary" disabled={!link} onClick={addLink}><Icon name="plus" size={14} />Apply</button>
      </div>
      <input placeholder="Search every application: company, job title, any question or answer" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="row wrap">
        <Seg<Tab> value={tab} onChange={setTab} options={[["everything", "Everything"], ["needs_you", `Needs you${s?.needs_you ? ` (${s.needs_you})` : ""}`], ["not_sent", `Not sent${s?.not_sent ? ` (${s.not_sent})` : ""}`], ["sent", `Sent${s?.sent_today ? ` (${s.sent_today} today)` : ""}`]]} />
        <span className="spacer" />
        <select style={{ width: "auto" }} value={resp} onChange={(e) => setResp(e.target.value)}>
          <option value="">Any response</option>
          <option value="none">No response yet</option>
          <option value="interview">Interview</option>
          <option value="assessment">Assessment</option>
          <option value="rejection">Rejected</option>
          <option value="receipt">Received</option>
          <option value="offer">Offer</option>
        </select>
        <select style={{ width: "auto" }} value={days} onChange={(e) => setDays(e.target.value)}>
          <option value="0">All time</option>
          <option value="1">Today</option>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
        </select>
      </div>
      <div className="small faint">{rows ? `${rows.length} applications` : "Loading…"}</div>
      <div className="card flush">
        {rows && rows.length === 0 && <div className="empty">No applications here.</div>}
        {waiting.length > 0 && <><div className="section-label">Still waiting on us</div><AppList rows={waiting} onChange={refresh} /></>}
        {sent.length > 0 && <><div className="section-label">Sent to the employer</div><AppList rows={sent} onChange={refresh} /></>}
      </div>
    </div>
  );
}

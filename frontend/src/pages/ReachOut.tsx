import { useState } from "react";
import { ago, api } from "../api";
import { Icon, RESPONSE_LABEL, usePoll, useToast } from "../ui";

function Copy({ text, label }: { text: string; label: string }) {
  const toast = useToast();
  return <button className="ghost" onClick={() => navigator.clipboard.writeText(text).then(() => toast(`${label} copied`))}><Icon name="copy" size={14} />Copy</button>;
}

export default function ReachOut() {
  const toast = useToast();
  const [rows, refresh] = usePoll<any[]>(() => api("/api/outreach"), 30000);
  const [busy, setBusy] = useState<number | null>(null);
  const draft = async (id: number) => {
    setBusy(id);
    try { await api(`/api/outreach/${id}`, { body: {} }); refresh(); }
    catch (e: any) { toast(e.message); }
    finally { setBusy(null); }
  };
  return (
    <div className="page">
      <div>
        <h1>Reach out</h1>
        <p className="muted small">A short note to a recruiter or hiring manager roughly doubles the odds someone reads your application. Draft one per job, find the person on LinkedIn, and send it yourself.</p>
      </div>
      <div className="card flush list">
        {rows && rows.length === 0 && <div className="empty">Once applications are sent, they show up here.</div>}
        {rows?.map((r) => (
          <div className="item" key={r.id} style={{ padding: "14px 16px" }}>
            <div className="row wrap">
              <span className="company">{r.company}</span><span className="small muted">{r.title}</span>
              {r.response && RESPONSE_LABEL[r.response] && <span className={`badge ${RESPONSE_LABEL[r.response][1]}`}>{RESPONSE_LABEL[r.response][0]}</span>}
              <span className="tiny faint">applied {ago(r.sent_at)}</span>
              <span className="spacer" />
              <a className="small" href={r.links.recruiters} target="_blank" rel="noreferrer">Find recruiters ↗</a>
              <a className="small" href={r.links.managers} target="_blank" rel="noreferrer">Hiring managers ↗</a>
              <button disabled={busy === r.id} onClick={() => draft(r.id)}>{busy === r.id ? "Writing…" : r.linkedin_note ? "Rewrite" : "Draft a note"}</button>
            </div>
            {r.linkedin_note && (
              <div className="grid2" style={{ marginTop: 12 }}>
                <div className="entry">
                  <div className="row small"><b>LinkedIn note</b><span className="faint">{r.linkedin_note.length}/300</span><span className="spacer" /><Copy text={r.linkedin_note} label="Note" /></div>
                  <p className="small" style={{ whiteSpace: "pre-wrap" }}>{r.linkedin_note}</p>
                </div>
                <div className="entry">
                  <div className="row small"><b>Email</b><span className="spacer" /><Copy text={`Subject: ${r.email_subject}\n\n${r.email_body}`} label="Email" /></div>
                  <p className="small"><b>{r.email_subject}</b></p>
                  <p className="small" style={{ whiteSpace: "pre-wrap" }}>{r.email_body}</p>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

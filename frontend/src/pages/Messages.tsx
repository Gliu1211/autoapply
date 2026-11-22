import { useState } from "react";
import { ago, api } from "../api";
import { RESPONSE_LABEL, Seg, usePoll, useToast } from "../ui";

type Cat = "" | "interview" | "assessment" | "rejection" | "action_needed" | "receipt";

export default function Messages({ state }: { state: any }) {
  const toast = useToast();
  const [cat, setCat] = useState<Cat>("");
  const [open, setOpen] = useState<number | null>(null);
  const [rows, refresh] = usePoll<any[]>(() => api(`/api/messages?category=${cat}`), 20000, [cat]);
  const [syncing, setSyncing] = useState(false);
  const sync = async () => {
    setSyncing(true);
    try { const r = await api("/api/inbox/sync", { body: {} }); toast(`${r.new} new emails`); refresh(); }
    catch (e: any) { toast(e.message); }
    finally { setSyncing(false); }
  };
  return (
    <div className="page">
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div className="spacer">
          <h1>Messages</h1>
          <p className="muted small">Employer email from your Gmail, sorted and matched to the application it's about.</p>
        </div>
        <button disabled={syncing || !state?.gmail?.configured} onClick={sync}>{syncing ? "Checking…" : "Check now"}</button>
      </div>
      {!state?.gmail?.configured && <div className="banner info small">Connect Gmail in <a href="#/settings">Settings</a> to see replies here.</div>}
      <Seg<Cat> value={cat} onChange={setCat} options={[["", "All"], ["interview", "Interviews"], ["assessment", "Assessments"], ["action_needed", "Action needed"], ["rejection", "Rejections"], ["receipt", "Receipts"]]} />
      <div className="card flush list">
        {rows && rows.length === 0 && <div className="empty">No messages yet.</div>}
        {rows?.map((m) => (
          <div className="item" key={m.id}>
            <div className="item-h" onClick={() => { setOpen(open === m.id ? null : m.id); if (!m.is_read) api(`/api/messages/${m.id}/read`, { body: {} }); }}>
              <div className="col spacer" style={{ gap: 2, minWidth: 0 }}>
                <div className="row wrap">
                  <span className={`badge ${RESPONSE_LABEL[m.category]?.[1] || ""}`}>{RESPONSE_LABEL[m.category]?.[0] || "Other"}</span>
                  <span className="company" style={{ fontWeight: m.is_read ? 500 : 650 }}>{m.company || m.sender}</span>
                  <span className="small muted">{m.subject}</span>
                  <span className="spacer" /><span className="tiny faint">{ago(m.received_at)}</span>
                </div>
                <div className="small muted">{m.summary}</div>
              </div>
            </div>
            {open === m.id && <div className="item-body"><div className="tiny faint">{m.sender}</div><p className="small" style={{ whiteSpace: "pre-wrap" }}>{m.body}</p></div>}
          </div>
        ))}
      </div>
    </div>
  );
}

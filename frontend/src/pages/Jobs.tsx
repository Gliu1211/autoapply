import { useState } from "react";
import { ago, api } from "../api";
import { Seg, usePoll, useToast } from "../ui";

type Tab = "matched" | "below" | "filtered" | "pending" | "applied";

export default function Jobs() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("matched");
  const [q, setQ] = useState("");
  const [data, refresh] = usePoll<any>(() => api(`/api/jobs?status=${tab}&q=${encodeURIComponent(q)}&limit=200`), 15000, [tab, q]);
  const counts = data?.counts || {};
  const act = async (id: number, what: "apply" | "skip") => {
    try {
      await api(`/api/jobs/${id}/${what}`, { body: {} });
      toast(what === "apply" ? "Queued. It goes out next." : "Skipped");
      refresh();
    } catch (e: any) { toast(e.message); }
  };
  return (
    <div className="page">
      <div>
        <h1>Jobs</h1>
        <p className="muted small">Every posting found across company job boards, new-grad and internship feeds, Workday sites and Handshake, and how it scored against your profile. Autopilot applies to the best matches on its own; you can also pick any here.</p>
      </div>
      <input placeholder="Search company, title, location" value={q} onChange={(e) => setQ(e.target.value)} />
      <Seg<Tab> value={tab} onChange={setTab} options={[
        ["matched", "Good matches"], ["below", `Below your bar${counts.scored ? "" : ""}`],
        ["pending", `Not scored yet${(counts.candidate || 0) + (counts.new || 0) ? ` (${(counts.candidate || 0) + (counts.new || 0)})` : ""}`],
        ["filtered", `Filtered out${counts.filtered ? ` (${counts.filtered})` : ""}`], ["applied", "Applied"],
      ]} />
      <div className="card flush list">
        {data && data.jobs.length === 0 && <div className="empty">Nothing here yet. Press “Apply to new jobs now” on Home to search.</div>}
        {data?.jobs.map((j: any) => (
          <div className="item" key={j.id}>
            <div className="item-h" style={{ cursor: "default" }}>
              <div className="col spacer" style={{ gap: 2, minWidth: 0 }}>
                <div className="row wrap">
                  <span className="company">{j.company}</span>
                  <a className="small" href={j.url} target="_blank" rel="noreferrer">{j.title}</a>
                  {j.score != null && <span className={`match ${j.score >= 65 ? "hi" : ""}`}>{j.score}% match</span>}
                </div>
                <div className="small muted">{j.score_reason || j.filter_reason || ""}</div>
                <div className="tiny faint">{[j.location, j.ats, j.source !== j.ats ? `via ${j.source}` : "", j.salary, j.posted_at ? `posted ${ago(j.posted_at)}` : ""].filter(Boolean).join(" · ")}</div>
              </div>
              {j.status === "queued" ? <span className="badge accent">Queued</span> : tab !== "applied" && (
                <div className="row">
                  <button className="primary" onClick={() => act(j.id, "apply")}>Apply</button>
                  {tab !== "filtered" && <button className="ghost" onClick={() => act(j.id, "skip")}>Skip</button>}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

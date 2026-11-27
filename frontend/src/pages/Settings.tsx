import { useEffect, useRef, useState } from "react";
import { ago, api } from "../api";
import { Field, Icon, Toggle, useToast } from "../ui";

const SOURCES: [string, string, string][] = [
  ["greenhouse", "Greenhouse boards", "Public job boards of a few hundred companies, growing as new ones are found."],
  ["lever", "Lever boards", "Off by default: Lever shows bot CAPTCHAs to automated browsers. Use the extension for Lever jobs."],
  ["ashby", "Ashby boards", ""],
  ["workable", "Workable boards", ""],
  ["workday", "Workday career sites", "Searched with your roles. Applying creates a candidate account per company."],
  ["simplify_feeds", "New-grad and internship feeds", "Community-maintained lists of new-grad and intern openings."],
  ["handshake", "Handshake", "Searches and applies in AutoApply's browser window with your Handshake sign-in. Sign in under Accounts first."],
];

const DIMS: Record<string, [string, string]> = {
  role: ["Role", "your roles above"],
  company: ["Company type", "the types you turned on"],
  site: ["Site", "the order under Where to look"],
};

export default function Settings() {
  const toast = useToast();
  const [d, setD] = useState<any>(null);
  const [gm, setGm] = useState({ address: "", app_password: "" });
  const [board, setBoard] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [opening, setOpening] = useState(false);   // hooks must all run before the early "Loading…" return
  const [roles, setRoles] = useState<string[] | null>(null);
  const [newRole, setNewRole] = useState("");
  const load = () => api("/api/settings").then((r) => { setD(r); setGm((g) => ({ ...g, address: g.address || r.gmail.address })); });
  useEffect(() => { load(); }, []);
  useEffect(() => { api("/api/profile").then((r) => setRoles(r.profile.looking_for.roles || [])).catch(() => setRoles([])); }, []);
  // While new companies are being sorted in the background, refresh the counts for a minute.
  const polls = useRef(0);
  useEffect(() => {
    if (!d?.company_types?.pending || !(d.settings.company_types || []).length || polls.current >= 12) return;
    const t = setTimeout(() => { polls.current += 1; load(); }, 5000);
    return () => clearTimeout(t);
  }, [d]);
  const saveRoles = async (next: string[]) => {
    const prev = roles;
    setRoles(next);
    try { await api("/api/profile", { method: "PUT", body: { looking_for: { roles: next } } }); }
    catch (e: any) { setRoles(prev); toast(e.message); }
  };
  if (!d) return <div className="page"><div className="empty">Loading…</div></div>;
  const s = d.settings;
  const put = async (patch: any) => {
    try { const r = await api("/api/settings", { method: "PUT", body: patch }); setD({ ...d, settings: r.settings }); }
    catch (e: any) { toast(e.message); }
  };
  const putTypes = async (patch: any) => { polls.current = 0; await put(patch); load(); };   // reload: counts change
  const ct: { types: { key: string; label: string; hint: string; jobs: number }[]; pending: number } = d.company_types || { types: [], pending: 0 };
  const wanted: string[] = (s.company_types || []).filter((k: string) => ct.types.some((t) => t.key === k));
  const dims: string[] = [...(s.priority_order || []).filter((k: string) => k in DIMS), ...Object.keys(DIMS)]
    .filter((k, i, a) => a.indexOf(k) === i);
  const num = (k: string, label: string, hint?: string) => (
    <Field label={label} hint={hint}><input type="number" min={0} value={s[k]} onChange={(e) => put({ [k]: Number(e.target.value) })} /></Field>
  );
  const connectGmail = async () => {
    try { await api("/api/gmail", { body: gm }); toast("Gmail connected"); setGm({ ...gm, app_password: "" }); load(); }
    catch (e: any) { toast(e.message); }
  };
  const openBrowser = async (url: string) => {
    setOpening(true);
    toast("Starting AutoApply's browser window…");
    try {
      const r = await api("/api/browser/open", { body: { url } });
      toast(`AutoApply's ${r.browser} window is open. If you don't see it, click its icon in the taskbar.`);
    } catch (e: any) { toast(e.message); }
    finally { setOpening(false); }
  };
  const boardTotals: Record<string, { ok: number; jobs: number; missing: number }> = {};
  for (const b of d.boards) {
    const t = (boardTotals[b.ats] ||= { ok: 0, jobs: 0, missing: 0 });
    if (b.status === "missing") t.missing += b.n; else { t.ok += b.n; t.jobs += b.jobs || 0; }
  }

  return (
    <div className="page">
      <div><h1>Settings</h1><p className="muted small">How AutoApply searches, how fast it sends, and the accounts it uses. Everything stays on this computer.</p></div>

      <section className="card col" style={{ gap: 14 }}>
        <h2>Pace</h2>
        <div className="grid3">
          {num("interval_minutes", "Search every (minutes)")}
          {num("daily_cap", "Max applications per day")}
          {num("min_match", "Minimum match %", "Jobs scoring lower are not applied to.")}
          {num("per_company_30d", "Max per company, 30 days")}
          {num("company_gap_days", "Days between same company")}
          {num("max_job_age_days", "Ignore postings older than (days)")}
          {num("concurrency", "Forms filled at once", "1–3. More is faster but busier.")}
        </div>
        <div className="grid2">
          <Field label="When a form is filled in">
            <select value={s.submit_mode} onChange={(e) => put({ submit_mode: e.target.value })}>
              <option value="auto">Submit it (fully autonomous)</option>
              <option value="review">Wait for my OK first</option>
            </select>
          </Field>
          <Field label="When a required question can't be answered from your profile">
            <select value={s.blank_required} onChange={(e) => put({ blank_required: e.target.value })}>
              <option value="try">Submit anyway and let the form decide</option>
              <option value="ask">Ask me first</option>
            </select>
          </Field>
        </div>
        <div className="row"><Toggle on={s.write_cover_letters} onChange={(v) => put({ write_cover_letters: v })} /><span className="small">Write a tailored cover letter when a form has a cover letter upload</span></div>
        <div className="row"><Toggle on={!s.headless} onChange={(v) => put({ headless: !v })} /><span className="small">Show the browser window <span className="muted">(needed to sign into Handshake, solve a CAPTCHA, or finish a form yourself)</span></span></div>
        <div className="grid2">
          <Field label="Cost" hint={({
            quality: "Opus for everything. About $0.004 per job scored; forms ~$0.01–0.08 each.",
            balanced: "Haiku scores jobs and sorts email (same apply/skip call as Opus 9 times in 10, ~40× cheaper); Opus writes every answer.",
            saver: "Like Balanced, plus Sonnet writes answers and cover letters: roughly half the cost per form, slightly plainer writing.",
          } as Record<string, string>)[s.cost_mode || "balanced"]}>
            <select value={s.cost_mode || "balanced"} onChange={(e) => put({ cost_mode: e.target.value })}>
              <option value="quality">Best quality</option>
              <option value="balanced">Balanced (recommended)</option>
              <option value="saver">Saver</option>
            </select>
          </Field>
          <Field label="AutoApply's browser" hint="The separate window autopilot works in. Each browser keeps its own sign-ins, so sign into Handshake again after switching.">
            <select value={s.browser || "chrome"} onChange={(e) => put({ browser: e.target.value })}>
              <option value="chrome">Google Chrome</option>
              <option value="msedge">Microsoft Edge</option>
            </select>
          </Field>
        </div>
      </section>

      <section className="card col" style={{ gap: 12 }}>
        <h2>What to apply to first</h2>
        <p className="small muted" style={{ marginTop: -6 }}>Your roles, most wanted first. Each job counts under the highest role its title matches (an “ML Engineer” or “LLM Engineer” posting counts as an AI engineer job). Among jobs that clear your minimum match, higher roles are scored, queued and sent first. Jobs matching none of these are filtered out. Same list as Profile → Looking for.</p>
        {roles === null ? <div className="small muted">Loading…</div> : roles.length === 0 ? <div className="small muted">No roles yet. Add one below or upload your resume in Profile.</div> : roles.map((r, i) => {
          const move = (dir: number) => { const n = [...roles]; const [x] = n.splice(i, 1); n.splice(i + dir, 0, x); saveRoles(n); };
          return (
            <div className="row" key={r + i}>
              <span className={`badge ${i === 0 ? "accent" : ""}`} style={{ minWidth: 34, justifyContent: "center" }}>{i + 1}</span>
              <span className="small spacer"><b>{r}</b></span>
              <button className="ghost" aria-label={`Move ${r} up`} disabled={i === 0} onClick={() => move(-1)}><Icon name="up" size={14} /></button>
              <button className="ghost" aria-label={`Move ${r} down`} disabled={i === roles.length - 1} onClick={() => move(1)}><Icon name="down" size={14} /></button>
              <button className="ghost" aria-label={`Remove ${r}`} onClick={() => saveRoles(roles.filter((_, k) => k !== i))}><Icon name="trash" size={14} /></button>
            </div>
          );
        })}
        {roles !== null && (
          <form className="row" onSubmit={(e) => {
            e.preventDefault();
            const v = newRole.trim();
            if (!v || roles.some((x) => x.toLowerCase() === v.toLowerCase())) return;
            saveRoles([...roles, v]); setNewRole("");
          }}>
            <input placeholder="Add a role, e.g. AI Engineer" value={newRole} onChange={(e) => setNewRole(e.target.value)} />
            <button disabled={!newRole.trim()}><Icon name="plus" size={14} />Add</button>
          </form>
        )}

        <div className="sep" style={{ margin: "2px 0" }} />
        <div className="col" style={{ gap: 2 }}>
          <b className="small">Company types</b>
          <p className="small muted" style={{ margin: 0 }}>Turn on the kinds of company you want, most wanted first. Claude sorts each employer once from what it knows; a division counts as its parent (Waymo is big tech, GDIT is defense), and companies it doesn't recognize get no type.{ct.pending > 0 && wanted.length > 0 && <b> Sorting {ct.pending} companies…</b>}</p>
        </div>
        {[...wanted.map((k) => ct.types.find((t) => t.key === k)!).filter(Boolean), ...ct.types.filter((t) => !wanted.includes(t.key))].map((t) => {
          const i = wanted.indexOf(t.key);
          const on = i >= 0;
          const move = (dir: number) => { const n = [...wanted]; const [x] = n.splice(i, 1); n.splice(i + dir, 0, x); putTypes({ company_types: n }); };
          return (
            <div className="row" key={t.key} style={{ alignItems: "flex-start", opacity: on ? 1 : 0.6 }}>
              <span className={`badge ${i === 0 ? "accent" : ""}`} style={{ minWidth: 34, justifyContent: "center" }}>{on ? i + 1 : "–"}</span>
              <Toggle on={on} onChange={(v) => putTypes({ company_types: v ? [...wanted, t.key] : wanted.filter((k) => k !== t.key) })} />
              <div className="spacer">
                <div className="small"><b>{t.label}</b><span className="muted"> · {t.jobs} open jobs</span></div>
                <div className="tiny muted">{t.hint}</div>
              </div>
              {on && <>
                <button className="ghost" aria-label={`Move ${t.label} up`} disabled={i === 0} onClick={() => move(-1)}><Icon name="up" size={14} /></button>
                <button className="ghost" aria-label={`Move ${t.label} down`} disabled={i === wanted.length - 1} onClick={() => move(1)}><Icon name="down" size={14} /></button>
              </>}
            </div>
          );
        })}
        <Field label="Companies of other types" hint={!wanted.length ? "Turn on a company type first." : s.company_types_only
          ? "Autopilot skips them (and companies it couldn't sort). Jobs you pick yourself still go through."
          : "Applied to after the types above, so nothing good is missed."}>
          <select value={s.company_types_only ? "only" : "after"} disabled={!wanted.length} onChange={(e) => putTypes({ company_types_only: e.target.value === "only" })}>
            <option value="after">Still apply to them, after these</option>
            <option value="only">Skip them</option>
          </select>
        </Field>

        <div className="sep" style={{ margin: "2px 0" }} />
        <div className="col" style={{ gap: 2 }}>
          <b className="small">When these disagree</b>
          <p className="small muted" style={{ margin: 0 }}>The top one decides; the next ones only break ties. Now: {dims.map((k) => DIMS[k][0].toLowerCase()).join(", then ")}.{" "}
            {dims[0] === "role" ? "Any AI engineer job goes before any Software Engineer job, wherever it is." : dims[0] === "company" ? "Any job at your first company type goes before the rest, whatever the role." : "Any job on your first site goes before the rest, whatever the role."}</p>
        </div>
        {dims.map((k, i) => {
          const move = (dir: number) => { const n = [...dims]; const [x] = n.splice(i, 1); n.splice(i + dir, 0, x); put({ priority_order: n }); };
          return (
            <div className="row" key={k}>
              <span className={`badge ${i === 0 ? "accent" : ""}`} style={{ minWidth: 34, justifyContent: "center" }}>{i + 1}</span>
              <span className="small spacer"><b>{DIMS[k][0]}</b> <span className="muted">· {DIMS[k][1]}</span></span>
              <button className="ghost" aria-label={`Move ${DIMS[k][0]} up`} disabled={i === 0} onClick={() => move(-1)}><Icon name="up" size={14} /></button>
              <button className="ghost" aria-label={`Move ${DIMS[k][0]} down`} disabled={i === dims.length - 1} onClick={() => move(1)}><Icon name="down" size={14} /></button>
            </div>
          );
        })}
      </section>

      <section className="card col" style={{ gap: 12 }}>
        <h2>Where to look</h2>
        <p className="small muted" style={{ marginTop: -6 }}>In priority order. Among jobs that clear your minimum match, sites higher up are scored, queued and applied to first, and if the same role shows up on two sites it's sent from the higher one only. Use the arrows to reorder.</p>
        {(() => {
          const order: string[] = s.source_priority || [];
          const ranked = [...order, ...SOURCES.map(([k]) => k).filter((k) => k !== "simplify_feeds" && !order.includes(k))];
          const move = (i: number, d: number) => {
            const n = [...ranked]; const [x] = n.splice(i, 1); n.splice(i + d, 0, x);
            put({ source_priority: n });
          };
          return ranked.map((k, i) => {
            const [, label, hint] = SOURCES.find(([key]) => key === k) || [k, k, ""];
            const on = !!s.sources[k];
            return (
              <div className="row" key={k} style={{ alignItems: "flex-start", opacity: on ? 1 : 0.6 }}>
                <span className={`badge ${on && i === 0 ? "accent" : ""}`} style={{ minWidth: 34, justifyContent: "center" }}>{i + 1}</span>
                <Toggle on={on} onChange={(v) => put({ sources: { [k]: v } })} />
                <div className="spacer">
                  <div className="small"><b>{label}</b>{boardTotals[k] && <span className="muted"> · {boardTotals[k].ok} companies, {boardTotals[k].jobs} open jobs</span>}</div>
                  {hint && <div className="tiny muted">{hint}</div>}
                </div>
                <button className="ghost" aria-label={`Move ${label} up`} disabled={i === 0} onClick={() => move(i, -1)}><Icon name="up" size={14} /></button>
                <button className="ghost" aria-label={`Move ${label} down`} disabled={i === ranked.length - 1} onClick={() => move(i, 1)}><Icon name="down" size={14} /></button>
              </div>
            );
          });
        })()}
        <div className="sep" style={{ margin: "2px 0" }} />
        <div className="row" style={{ alignItems: "flex-start" }}>
          <Toggle on={!!s.sources.simplify_feeds} onChange={(v) => put({ sources: { simplify_feeds: v } })} />
          <div className="spacer">
            <div className="small"><b>New-grad and internship feeds</b></div>
            <div className="tiny muted">Community-maintained lists of new-grad and intern openings. Each job counts under the site you apply on (Greenhouse, Workday…), so it follows the order above.</div>
          </div>
        </div>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <Toggle on={s.supported_only !== false} onChange={(v) => put({ supported_only: v })} />
          <div className="spacer">
            <div className="small"><b>Autopilot only applies on sites it handles well</b></div>
            <div className="tiny muted">Greenhouse, Ashby, Workable, Workday and Handshake. Jobs on company career portals (Oracle, iCIMS, SmartRecruiters, Eightfold…) are skipped; open those yourself and use the extension. Links you paste or pick yourself always go through.</div>
          </div>
        </div>
        <div className="row">
          <input placeholder="Add a company: paste its Greenhouse, Lever, Ashby, Workable or Workday careers link" value={board} onChange={(e) => setBoard(e.target.value)} />
          <button disabled={!board} onClick={() => api("/api/boards", { body: { url: board } }).then((r) => { toast(`Added ${r.ats} board “${r.token.split("|")[0]}”`); setBoard(""); load(); }).catch((e) => toast(e.message))}>Add</button>
        </div>
      </section>

      <section className="card col" style={{ gap: 14 }} id="accounts">
        <h2>Accounts</h2>

        <div className="col" style={{ gap: 8 }}>
          <div className="row"><Icon name="mail" /><b>Gmail</b>{
            !d.gmail.configured ? <span className="badge">Not connected</span>
              : d.gmail.ok === false ? <span className="badge bad">Login rejected · {d.gmail.address}</span>
              : d.gmail.ok ? <span className="badge good">Connected · {d.gmail.address}</span>
              : <span className="badge">Saved · not checked yet</span>}</div>
          {d.gmail.ok === false && <div className="banner bad small">{d.gmail.error}</div>}
          <p className="small muted">Reads verification codes, Workday account-confirmation links and employer replies. Uses IMAP with an app password (Google Account → Security → 2-Step Verification → App passwords). It only reads.</p>
          <div className="grid2">
            <input placeholder="you@gmail.com" value={gm.address} onChange={(e) => setGm({ ...gm, address: e.target.value })} />
            <input placeholder="16-character app password" type="password" value={gm.app_password} onChange={(e) => setGm({ ...gm, app_password: e.target.value })} />
          </div>
          <div className="row">
            <button disabled={!gm.address || !gm.app_password} onClick={connectGmail}>{d.gmail.configured ? "Update" : "Connect Gmail"}</button>
            <a className="small" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">Create an app password ↗</a>
          </div>
        </div>
        <div className="sep" />

        <div className="col" style={{ gap: 8 }}>
          <div className="row"><b>Handshake</b></div>
          <p className="small muted">Handshake uses your school login, so autopilot works inside <b>AutoApply's own browser window</b> (a separate {s.browser === "msedge" ? "Edge" : "Chrome"} window with its own profile, not your everyday browser). Click below, sign in once in the window that opens, and it stays signed in. It can take a few seconds to appear the first time, and Windows may open it behind your current window; check the taskbar.</p>
          <div className="row">
            <input value={s.handshake.base_url} onChange={(e) => put({ handshake: { base_url: e.target.value } })} />
            <button disabled={opening} onClick={() => openBrowser(`${s.handshake.base_url.replace(/\/$/, "")}/login`)}><Icon name="ext" size={14} />{opening ? "Opening…" : "Sign in to Handshake"}</button>
          </div>
        </div>
        <div className="sep" />

        <div className="col" style={{ gap: 8 }}>
          <div className="row"><b>Workday accounts</b><span className="badge">{d.accounts.length}</span></div>
          <p className="small muted">Each company on Workday needs its own candidate account. AutoApply creates them with your email and this password, so you can sign in yourself any time.</p>
          <div className="row">
            <input readOnly type={showPw ? "text" : "password"} value={d.account_password} />
            <button className="ghost" onClick={() => setShowPw(!showPw)}>{showPw ? "Hide" : "Show"}</button>
            <button className="ghost" onClick={() => navigator.clipboard.writeText(d.account_password).then(() => toast("Copied"))}><Icon name="copy" size={14} /></button>
          </div>
          {d.accounts.map((a: any) => (
            <div className="row small" key={a.host}><span>{a.host}</span><span className="faint">created {ago(a.created_at)}</span>{a.verified ? <span className="badge good">verified</span> : null}</div>
          ))}
        </div>
        <div className="sep" />

        <div className="col" style={{ gap: 8 }}>
          <div className="row"><b>Browser extension</b></div>
          <p className="small muted">Fill applications in your everyday browser (Chrome or Edge), with your own sign-ins, using this profile. On Greenhouse, Lever, Ashby, Workable, Workday and Handshake pages a "Fill with AutoApply" button appears; anywhere else, click the extension icon. You review and press Submit; it logs the application here.</p>
          <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Open <code>edge://extensions</code> (Edge) or <code>chrome://extensions</code> (Chrome) and turn on <b>Developer mode</b>.</li>
            <li>Click <b>Load unpacked</b> and choose the <code>extension</code> folder inside the AutoApply folder.</li>
            <li>Pin it from the puzzle-piece menu. It needs this app running to work.</li>
          </ol>
        </div>
        <div className="sep" />

        <div className="row"><b>Claude</b>{d.llm ? <span className="badge good">API key found</span> : <span className="badge bad">No API key: set ANTHROPIC_API_KEY in .env</span>}</div>
      </section>
    </div>
  );
}

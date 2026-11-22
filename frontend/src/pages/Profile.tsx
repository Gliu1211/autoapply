import { useEffect, useRef, useState } from "react";
import { ago, api } from "../api";
import { Chips, Field, Icon, Pick, useToast, YesNo } from "../ui";

const SECTIONS = [
  ["resume", "Resume"], ["contact", "Contact"], ["looking", "Looking for"], ["eligibility", "Eligibility"],
  ["about", "About you"], ["eeo", "Self-identification"], ["languages", "Languages"], ["experience", "Experience"],
  ["education", "Education"], ["projects", "Projects"], ["skills", "Skills"], ["remembered", "Remembered answers"],
];
const LEVELS = ["Internship", "Entry level", "Mid level", "Senior", "Staff+", "Manager"];
const STATUS = ["U.S. citizen", "U.S. permanent resident (green card)", "H-1B visa", "F-1 student (OPT / STEM OPT)", "TN visa", "L-1 visa", "O-1 visa", "E-3 visa", "Other work authorization", "Not authorized to work in the U.S."];
const EEO: [string, string, string[]][] = [
  ["gender", "Gender", ["Decline to self-identify", "Male", "Female", "Non-binary"]],
  ["race", "Race / ethnicity", ["Decline to self-identify", "Asian", "East Asian", "South Asian", "Southeast Asian", "Black or African American", "Hispanic or Latino", "White", "American Indian or Alaska Native", "Native Hawaiian or Other Pacific Islander", "Two or More Races"]],
  ["hispanic", "Hispanic or Latino?", ["Decline to self-identify", "Yes", "No"]],
  ["veteran", "Veteran status", ["I am not a protected veteran", "I identify as one or more of the classifications of protected veteran", "I don't wish to answer"]],
  ["orientation", "Sexual orientation", ["Decline to self-identify", "Heterosexual / Straight", "Gay or Lesbian", "Bisexual", "Queer", "Asexual", "Other"]],
  ["transgender", "Transgender?", ["Decline to self-identify", "No", "Yes"]],
  ["disability", "Disability", ["No, I do not have a disability and have not had one in the past", "Yes, I have a disability (or previously had a disability)", "I don't wish to answer"]],
];
const YN: [string, string, string, boolean?][] = [
  ["authorized_us", "Authorized to work in the US?", ""],
  ["need_sponsorship", "Need visa sponsorship now or later?", ""],
  ["willing_relocate", "Willing to relocate?", "Yes means in-office jobs in any US city, not just the ones you listed."],
  ["open_hybrid_onsite", "Open to hybrid / in-office?", ""],
  ["over_18", "18 or older?", "Nearly every form asks."],
  ["ok_to_text", "OK for employers to text you?", "Yes gets you replies faster."],
  ["felony", "Ever convicted of a felony?", "Blank means we ask you each time.", true],
  ["drivers_license", "Valid driver's license?", "Blank means we ask you each time.", true],
  ["dismissed", "Ever dismissed or asked to resign for misconduct?", "Blank means we ask you each time.", true],
  ["tobacco", "Use tobacco or nicotine?", "Blank means we ask you each time.", true],
  ["relocation_assistance", "Need relocation assistance to move?", "Blank means we ask you each time.", true],
  ["non_compete", "Bound by a non-compete or non-solicit?", "Blank means we ask you each time.", true],
];

type P = any;

function ListEditor({ items, fields, onChange, addLabel, title }: { items: any[]; fields: [string, string, string?][]; onChange: (v: any[]) => void; addLabel: string; title: (x: any) => string }) {
  const [open, setOpen] = useState<number | null>(null);
  const set = (i: number, k: string, v: any) => onChange(items.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  const move = (i: number, d: number) => {
    const n = [...items]; const [x] = n.splice(i, 1); n.splice(i + d, 0, x); onChange(n);
  };
  return (
    <div className="col">
      {items.map((it, i) => (
        <div className="entry" key={i}>
          <div className="row">
            <button className="link" style={{ fontWeight: 600, color: "var(--text)", textAlign: "left" }} onClick={() => setOpen(open === i ? null : i)}>{title(it) || "(untitled)"}</button>
            <span className="spacer" />
            <button className="ghost" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="up" size={14} /></button>
            <button className="ghost" aria-label="Move down" disabled={i === items.length - 1} onClick={() => move(i, 1)}><Icon name="down" size={14} /></button>
            <button className="ghost danger" aria-label="Delete" onClick={() => onChange(items.filter((_, j) => j !== i))}><Icon name="trash" size={14} /></button>
          </div>
          {open === i && (
            <div className="grid2">
              {fields.map(([k, label, kind]) => (
                <div key={k} style={kind === "long" ? { gridColumn: "1 / -1" } : undefined}>
                  <Field label={label}>
                    {kind === "long" ? <textarea rows={4} value={it[k] || ""} onChange={(e) => set(i, k, e.target.value)} />
                      : <input value={it[k] || ""} onChange={(e) => set(i, k, e.target.value)} />}
                  </Field>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      <div><button onClick={() => { onChange([...items, {}]); setOpen(items.length); }}><Icon name="plus" size={14} />{addLabel}</button></div>
    </div>
  );
}

function Remembered() {
  const toast = useToast();
  const [rows, setRows] = useState<any[]>([]);
  const [q, setQ] = useState(""); const [a, setA] = useState("");
  const load = () => api("/api/remembered").then(setRows);
  useEffect(() => { load(); }, []);
  return (
    <div className="col" style={{ gap: 0 }}>
      {rows.map((r) => (
        <div key={r.id} className="row" style={{ alignItems: "flex-start", padding: "10px 0", borderTop: "1px solid var(--line-2)" }}>
          <div className="spacer col" style={{ gap: 4 }}>
            <div className="small muted">{r.question}</div>
            <input defaultValue={r.answer} onBlur={(e) => e.target.value !== r.answer && api(`/api/remembered/${r.id}`, { method: "PUT", body: { answer: e.target.value } }).then(() => toast("Saved"))} />
          </div>
          <button className="ghost danger" aria-label="Forget" onClick={() => api(`/api/remembered/${r.id}`, { method: "DELETE" }).then(load)}><Icon name="trash" size={14} /></button>
        </div>
      ))}
      <div className="entry" style={{ marginTop: 10 }}>
        <div className="small"><b>Add an answer</b> <span className="muted">for a question that keeps coming up</span></div>
        <input placeholder="Question" value={q} onChange={(e) => setQ(e.target.value)} />
        <input placeholder="Answer" value={a} onChange={(e) => setA(e.target.value)} />
        <div><button disabled={!q || !a} onClick={() => api("/api/remembered", { body: { question: q, answer: a } }).then((r) => { setRows(r); setQ(""); setA(""); })}>Remember</button></div>
      </div>
    </div>
  );
}

export default function Profile() {
  const toast = useToast();
  const [p, setP] = useState<P | null>(null);
  const [summary, setSummary] = useState("");
  const [saved, setSaved] = useState("");
  const [uploading, setUploading] = useState(false);
  const [over, setOver] = useState(false);
  const timer = useRef<number>();
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api("/api/profile").then((r) => { setP(r.profile); setSummary(r.resume_summary); }); }, []);
  if (!p) return <div className="page"><div className="empty">Loading…</div></div>;

  const update = (next: P) => {
    setP(next);
    setSaved("Saving…");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try { await api("/api/profile", { method: "PUT", body: next }); setSaved("Saved"); }
      catch (e: any) { setSaved(""); toast(e.message); }
    }, 600);
  };
  const set = (section: string, key: string, v: any) => update({ ...p, [section]: { ...p[section], [key]: v } });
  const c = p.contact, lf = p.looking_for, el = p.eligibility;

  const upload = async (f?: File | null) => {
    if (!f) return;
    setUploading(true);
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api("/api/profile/resume", { form: fd });
      setP(r.profile); setSummary(r.resume_summary); toast(r.note);
    } catch (e: any) { toast(e.message); }
    finally { setUploading(false); }
  };

  const txt = (section: string, key: string, label: string, opts: { hint?: string; optional?: boolean; ph?: string } = {}) => (
    <Field label={label} hint={opts.hint} optional={opts.optional}>
      <input value={p[section][key] ?? ""} placeholder={opts.ph} onChange={(e) => set(section, key, e.target.value)} />
    </Field>
  );

  return (
    <div className="page" style={{ maxWidth: 1080 }}>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div className="spacer">
          <h1>{[c.first_name, c.last_name].filter(Boolean).join(" ") || "Your profile"}</h1>
          <p className="muted small">Everything AutoApply knows about you. Applications only ever use what's here.</p>
        </div>
        <span className="small faint">{saved}</span>
      </div>
      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <nav className="subnav">{SECTIONS.map(([id, l]) => <a key={id} href={`#/profile`} onClick={(e) => { e.preventDefault(); document.getElementById(`s-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{l}</a>)}</nav>
        <div className="col spacer" style={{ gap: 16, minWidth: 0 }}>

          <section className="card" id="s-resume">
            <h2>Resume</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Your main resume, attached exactly as you uploaded it. Uploading reads it and fills any blank sections below.</p>
            {p.resume && (
              <div className="row small" style={{ marginBottom: 10 }}>
                <Icon name="apps" /><b>{p.resume.filename}</b>
                <span className="muted">{Math.round((p.resume.size || 0) / 1024)} KB · uploaded {ago(p.resume.uploaded_at)} · {summary.split(" · ").slice(1).join(" · ")}</span>
              </div>
            )}
            <div className={`drop ${over ? "over" : ""}`} onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
              {uploading ? "Reading your resume…" : p.resume ? "Drop a new PDF here to replace it" : "Drop your resume PDF here, or click to choose"}
            </div>
            <input ref={fileRef} type="file" accept=".pdf,.doc,.docx" hidden onChange={(e) => upload(e.target.files?.[0])} />
          </section>

          <section className="card" id="s-contact">
            <h2>Contact</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>These go on every form, exactly as written.</p>
            <div className="grid2">
              {txt("contact", "first_name", "Legal first name")}
              {txt("contact", "last_name", "Legal last name")}
              {txt("contact", "preferred_name", "Preferred name", { optional: true })}
              {txt("contact", "email", "Email")}
              {txt("contact", "phone", "Phone")}
              {txt("contact", "address", "Street address", { hint: "Workday and many other forms won't submit without one." })}
              {txt("contact", "city", "City")}
              {txt("contact", "state", "State / region", { ph: "MD" })}
              {txt("contact", "zip", "ZIP / postal code")}
              {txt("contact", "country", "Country")}
              {txt("contact", "linkedin", "LinkedIn")}
              {txt("contact", "github", "GitHub", { optional: true })}
              {txt("contact", "website", "Website / portfolio", { optional: true })}
            </div>
          </section>

          <section className="card" id="s-looking">
            <h2>Looking for</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Decides which jobs you're matched with.</p>
            <div className="col" style={{ gap: 14 }}>
              <Field label="Roles" hint="Press Enter after each. Titles are expanded, e.g. “Backend Engineer” also matches “Software Engineer”.">
                <Chips value={lf.roles} onChange={(v) => set("looking_for", "roles", v)} placeholder="Add a role…" />
              </Field>
              <Field label="Level"><Pick value={lf.levels || []} options={LEVELS} onChange={(v) => set("looking_for", "levels", v)} /></Field>
              <Field label="Locations" optional hint="Cities you'd work in. Leave empty for anywhere in the US.">
                <Chips value={lf.locations} onChange={(v) => set("looking_for", "locations", v)} placeholder="Anywhere in the US" />
              </Field>
              <div className="grid2">
                <Field label="Remote">
                  <select value={lf.remote} onChange={(e) => set("looking_for", "remote", e.target.value)}>
                    <option>Remote or on-site</option><option>Remote only</option><option>On-site only</option>
                  </select>
                </Field>
                <Field label="Minimum base salary (USD)" optional hint="Used to rank, not to hide.">
                  <input value={lf.min_salary} onChange={(e) => set("looking_for", "min_salary", e.target.value)} />
                </Field>
              </div>
              <Field label="Companies to avoid" optional hint="Current employer, etc.">
                <Chips value={lf.avoid_companies} onChange={(v) => set("looking_for", "avoid_companies", v)} placeholder="Add a company…" />
              </Field>
              <Field label="Standing instructions" optional hint="Anything filters can't say. Every job is checked against this before applying.">
                <textarea value={lf.standing_instructions} placeholder="e.g. Skip defense contractors. Prefer companies under 500 people." onChange={(e) => set("looking_for", "standing_instructions", e.target.value)} />
              </Field>
            </div>
          </section>

          <section className="card" id="s-eligibility">
            <h2>Eligibility</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Every application asks these. Answer once and they're never guessed.</p>
            <div className="grid2">
              {YN.map(([k, label, hint, opt]) => <Field key={k} label={label} hint={hint || undefined} optional={opt}><YesNo value={el[k]} onChange={(v) => set("eligibility", k, v)} /></Field>)}
              <Field label="Status" hint="Many forms ask which one.">
                <select value={el.work_status} onChange={(e) => set("eligibility", "work_status", e.target.value)}>
                  <option value="">Select…</option>{STATUS.map((x) => <option key={x}>{x}</option>)}
                </select>
              </Field>
              {txt("eligibility", "years_experience", "Years of professional experience", { ph: "0" })}
              <Field label="Earliest start date">
                <select value={el.start_date} onChange={(e) => set("eligibility", "start_date", e.target.value)}>
                  <option value="">Select…</option>{["Immediately", "Two weeks after offer", "One month after offer", "Flexible"].map((x) => <option key={x}>{x}</option>)}
                </select>
              </Field>
              {txt("eligibility", "notice_period", "Notice period", { optional: true, ph: "2 weeks" })}
              {txt("eligibility", "salary_expectation", "Salary expectation", { optional: true, hint: "What you say when a form asks." })}
              {txt("eligibility", "clearance", "Security clearance", { optional: true, ph: "Leave blank if none" })}
              {txt("eligibility", "current_employer", "Current employer", { optional: true, hint: "Blank = current role on your resume. N/A if not working." })}
              <Field label="Your degree" hint="For “highest level completed” questions.">
                <select value={el.degree_status} onChange={(e) => set("eligibility", "degree_status", e.target.value)}>
                  {["As on my resume", "Finished", "Still studying", "Left before finishing"].map((x) => <option key={x}>{x}</option>)}
                </select>
              </Field>
            </div>
            <label className="row small" style={{ marginTop: 14, alignItems: "flex-start", cursor: "pointer" }}>
              <input type="checkbox" style={{ width: "auto", marginTop: 3 }} checked={!!el.no_conflicts} onChange={(e) => set("eligibility", "no_conflicts", e.target.checked)} />
              <span><b>Answer “No” to conflict-of-interest questions.</b> <span className="muted">Worked at the company before, know someone there, government or military background, sanctioned by a regulator. Tick only if none of that is true for you.</span></span>
            </label>
          </section>

          <section className="card" id="s-about">
            <h2>About you</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Stories, motivations, what you're proud of that isn't on the resume. Used in open-ended answers and cover letters, and only what you write here.</p>
            <textarea rows={6} value={p.about} onChange={(e) => update({ ...p, about: e.target.value })} />
          </section>

          <section className="card" id="s-eeo">
            <h2>Voluntary self-identification</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>US employers are required to ask. Answers never affect your application, and declining is always a choice.</p>
            <div className="grid2">
              {EEO.map(([k, label, opts]) => (
                <Field key={k} label={label}>
                  <select value={p.eeo[k]} onChange={(e) => set("eeo", k, e.target.value)}>
                    <option value="">Not set (declines)</option>{opts.map((o) => <option key={o}>{o}</option>)}
                  </select>
                </Field>
              ))}
            </div>
          </section>

          <section className="card" id="s-languages">
            <h2>Languages</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>The languages you speak. Applications claim these and nothing else.</p>
            <div className="col">
              {p.languages.map((l: any, i: number) => (
                <div className="row" key={i}>
                  <input value={l.name} placeholder="Language" onChange={(e) => update({ ...p, languages: p.languages.map((x: any, j: number) => j === i ? { ...x, name: e.target.value } : x) })} />
                  <select style={{ width: 180 }} value={l.level} onChange={(e) => update({ ...p, languages: p.languages.map((x: any, j: number) => j === i ? { ...x, level: e.target.value } : x) })}>
                    {["Native", "Fluent", "Advanced", "Intermediate", "Beginner"].map((x) => <option key={x}>{x}</option>)}
                  </select>
                  <button className="ghost danger" aria-label="Remove" onClick={() => update({ ...p, languages: p.languages.filter((_: any, j: number) => j !== i) })}><Icon name="trash" size={14} /></button>
                </div>
              ))}
              <div><button onClick={() => update({ ...p, languages: [...p.languages, { name: "", level: "Fluent" }] })}><Icon name="plus" size={14} />Add a language</button></div>
            </div>
          </section>

          <section className="card" id="s-experience">
            <h2>Work experience</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Every application uses these exactly, and Workday's job rows are filled from them.</p>
            <ListEditor items={p.experience} onChange={(v) => update({ ...p, experience: v })} addLabel="Add a role"
              title={(x) => [x.title, x.company].filter(Boolean).join(" at ") + (x.start ? ` · ${x.start} – ${x.end || "Present"}` : "")}
              fields={[["title", "Title"], ["company", "Company"], ["location", "Location"], ["start", "Start"], ["end", "End"], ["description", "What you did", "long"]]} />
          </section>

          <section className="card" id="s-education">
            <h2>Education</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>In the order forms should list them.</p>
            <ListEditor items={p.education} onChange={(v) => update({ ...p, education: v })} addLabel="Add a school"
              title={(x) => `${x.school || ""}${x.degree ? ` · ${x.degree}${x.field ? `, ${x.field}` : ""}` : ""}${x.start ? ` · ${x.start} – ${x.end || ""}` : ""}`}
              fields={[["school", "School"], ["degree", "Degree"], ["field", "Field of study"], ["gpa", "GPA"], ["start", "Start"], ["end", "End (or expected)"]]} />
          </section>

          <section className="card" id="s-projects">
            <h2>Projects</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Written answers and cover letters draw on these. A project you add here is one the agent can talk about.</p>
            <ListEditor items={p.projects} onChange={(v) => update({ ...p, projects: v })} addLabel="Add a project"
              title={(x) => `${x.name || ""}${x.tech ? ` · ${x.tech}` : ""}${x.dates ? ` · ${x.dates}` : ""}`}
              fields={[["name", "Name"], ["tech", "Tech"], ["dates", "Dates"], ["description", "Description", "long"]]} />
          </section>

          <section className="card" id="s-skills">
            <h2>Skills</h2>
            <p className="small muted" style={{ marginBottom: 12 }}>Used for matching and for “do you have experience with X” questions.</p>
            <Chips value={p.skills} onChange={(v) => update({ ...p, skills: v })} placeholder="Add a skill…" />
          </section>

          <section className="card" id="s-remembered">
            <h2 className="row"><Icon name="clock" />Remembered answers</h2>
            <p className="small muted" style={{ marginBottom: 6 }}>When you answer or correct a question, it's reused on future forms.</p>
            <Remembered />
          </section>
        </div>
      </div>
    </div>
  );
}

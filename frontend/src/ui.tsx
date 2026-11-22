import { createContext, ReactNode, useContext, useEffect, useRef, useState } from "react";
import { ago } from "./api";

type ToastFn = (msg: string) => void;
const ToastCtx = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastHost({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const timer = useRef<number>();
  const show = (m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(""), 3800);
  };
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {msg && <div className="toast">{msg}</div>}
    </ToastCtx.Provider>
  );
}

export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []): [T | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const ref = useRef(load);
  ref.current = load;
  const refresh = () => { ref.current().then(setData).catch(() => {}); };
  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, ms);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return [data, refresh];
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} />
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="tablist">
      {options.map(([v, l]) => (
        <button key={v} role="tab" aria-selected={v === value} className={v === value ? "on" : ""} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}

export function Chips({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const parts = draft.split(",").map((s) => s.trim()).filter(Boolean).filter((s) => !value.includes(s));
    if (parts.length) onChange([...value, ...parts]);
    setDraft("");
  };
  return (
    <div className="chips">
      {value.map((v) => (
        <span className="chip" key={v}>{v}<button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))}>×</button></span>
      ))}
      <input value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
          else if (e.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
        }}
        onBlur={add} />
    </div>
  );
}

export function Pick({ value, options, onChange }: { value: string[]; options: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="pick">
      {options.map((o) => (
        <button type="button" key={o} className={value.includes(o) ? "on" : ""}
          onClick={() => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o])}>{o}</button>
      ))}
    </div>
  );
}

export function Field({ label, hint, optional, children }: { label: string; hint?: string; optional?: boolean; children: ReactNode }) {
  return (
    <label className="f">
      <span className="l">{label}{optional && <span className="faint" style={{ fontWeight: 400 }}> · optional</span>}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function YesNo({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value || ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select…</option>
      <option>Yes</option>
      <option>No</option>
    </select>
  );
}

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  const p: Record<string, ReactNode> = {
    home: <><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></>,
    jobs: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></>,
    apps: <><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5" /><path d="M10 13h6M10 17h6" /></>,
    reach: <><path d="M3 11v2a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1z" /><path d="M16 9a4 4 0 0 1 0 6" /></>,
    msg: <><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
    gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>,
    chev: <path d="m9 6 6 6-6 6" />,
    check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    alert: <><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16h.01" /></>,
    x: <><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></>,
    play: <path d="M7 4v16l13-8z" />,
    ext: <><path d="M14 4h6v6" /><path d="M20 4 10 14" /><path d="M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" /></>,
    mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>,
    copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    trash: <><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></>,
    up: <path d="m6 14 6-6 6 6" />,
    down: <path d="m6 10 6 6 6-6" />,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {p[name]}
    </svg>
  );
}

export function StatusLine({ status, detail, sentAt, updatedAt }: { status: string; detail: string; sentAt: string | null; updatedAt: string }) {
  if (status === "sent") return <span className="row small" style={{ color: "var(--good)" }}><Icon name="check" size={14} />Applied {ago(sentAt)}</span>;
  if (status === "applying") return <span className="row small" style={{ color: "var(--good)" }}><span className="spin" />Applying now <span className="faint">· {detail}</span></span>;
  if (status === "queued") return <span className="row small muted"><Icon name="clock" size={14} />Not sent yet <span className="badge">{detail?.startsWith("Waiting") ? detail : "Queued to send"}</span></span>;
  if (status === "ready") return <span className="row small" style={{ color: "var(--accent)" }}><Icon name="alert" size={14} />Ready for your OK <span className="faint">· filled {ago(updatedAt)}</span></span>;
  if (status === "needs_you") return <span className="row small" style={{ color: "var(--warn)" }}><Icon name="alert" size={14} />Needs you <span className="faint" style={{ color: "var(--warn)" }}>· {detail}</span></span>;
  if (status === "failed") return <span className="row small" style={{ color: "var(--bad)" }}><Icon name="x" size={14} />Didn't go out <span className="faint">· {detail}</span></span>;
  if (status === "in_browser") return <span className="row small" style={{ color: "var(--accent)" }}><Icon name="ext" size={14} />In your browser <span className="faint">· {detail}</span></span>;
  if (status === "skipped") return <span className="row small faint"><Icon name="x" size={14} />Skipped <span>· {detail}</span></span>;
  return <span className="small muted">{status}</span>;
}

export const RESPONSE_LABEL: Record<string, [string, string]> = {
  receipt: ["Received", ""],
  rejection: ["Rejected", "bad"],
  interview: ["Interview", "good"],
  assessment: ["Assessment", "accent"],
  offer: ["Offer", "good"],
  action_needed: ["Action needed", "warn"],
};

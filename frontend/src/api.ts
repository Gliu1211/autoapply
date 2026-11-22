export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const init: RequestInit = { method: opts.method || (opts.body || opts.form ? "POST" : "GET") };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, init);
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data as T;
}

export type AppRow = {
  id: number; status: string; status_detail: string; ats: string; created_at: string; started_at: string | null;
  sent_at: string | null; updated_at: string; attempts: number; blank_required: number; response: string | null;
  approved: number; job_id: number; company: string; title: string; location: string; score: number | null;
  score_reason: string | null; url: string; apply_url: string; posted_at: string | null; source: string;
  questions_answered: number; used_resume: boolean; shot: string | null;
};

export type Answer = { question: string; answer: string; source: string; required: boolean; kind: string; ok: boolean };
export type Pending = { question: string; kind: string; options: string[]; required: boolean };

export function ago(iso?: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "soon";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function until(iso?: string | null): string {
  if (!iso) return "";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 60) return "in a moment";
  if (s < 3600) return `in ${Math.round(s / 60)}m`;
  return `in ${Math.round(s / 3600)}h`;
}

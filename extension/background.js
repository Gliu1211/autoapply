// Talks to the AutoApply app running on this computer. Content scripts and the popup go through here,
// because the extension's host permission lets this worker reach 127.0.0.1 where a web page can't.
const DEFAULT_SERVER = "http://127.0.0.1:8742";

async function server() {
  const { server } = await chrome.storage.local.get("server");
  return (server || DEFAULT_SERVER).replace(/\/$/, "");
}

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function handle(msg) {
  const base = await server();
  if (msg.type === "api") {
    const init = { method: msg.method || (msg.body ? "POST" : "GET") };
    if (msg.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(msg.body);
    }
    const r = await fetch(base + msg.path, init);
    const text = await r.text();
    let data = text;
    try { data = text ? JSON.parse(text) : null; } catch { /* plain text */ }
    if (!r.ok) return { ok: false, error: (data && data.detail) || `${r.status} ${r.statusText}` };
    return { ok: true, data };
  }
  if (msg.type === "file") {
    const path = msg.kind === "resume" ? "/api/ext/resume" : `/api/ext/cover/${msg.appId}`;
    const r = await fetch(base + path);
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
      return { ok: false, error: detail };
    }
    const cd = r.headers.get("content-disposition") || "";
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i);
    const name = m ? decodeURIComponent(m[1]) : (msg.kind === "resume" ? "Resume.pdf" : "Cover_Letter.pdf");
    return { ok: true, data: { name, mime: r.headers.get("content-type") || "application/pdf", b64: toBase64(await r.arrayBuffer()) } };
  }
  if (msg.type === "dashboard") {
    await chrome.tabs.create({ url: base + (msg.path || "/") });
    return { ok: true };
  }
  return { ok: false, error: "unknown message" };
}

chrome.runtime.onMessage.addListener((msg, _sender, send) => {
  handle(msg).then(send, (e) => send({ ok: false, error: e && e.message ? (e.message.includes("Failed to fetch") ? "AutoApply isn't running on this computer. Start it with start.ps1." : e.message) : String(e) }));
  return true; // async response
});

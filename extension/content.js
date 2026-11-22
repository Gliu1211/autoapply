// AutoApply in-page filler. Reads the form with the same reader the autopilot uses (extract.js),
// asks the local app what goes in each box, fills it, and leaves Submit to you.
(() => {
  if (window.__autoapplyLoaded) return;
  window.__autoapplyLoaded = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9+ ]/g, " ").replace(/\s+/g, " ").trim();
  const isVis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const byId = (id) => document.querySelector(`[data-aa="${CSS.escape(id)}"]`);

  function send(msg) {
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => {
          if (chrome.runtime.lastError) return reject(new Error("AutoApply extension was reloaded; refresh this page."));
          if (!r || !r.ok) return reject(new Error((r && r.error) || "AutoApply isn't running on this computer."));
          resolve(r.data);
        });
      } catch (e) { reject(e); }
    });
  }
  const api = (path, body) => send({ type: "api", path, body, method: body ? "POST" : "GET" });

  // ---------- option matching (the server already resolves most answers to exact option text) ----------
  function matchOption(want, opts) {
    const wants = (Array.isArray(want) ? want : [want]).filter(Boolean).map(String);
    const n = opts.map(norm);
    for (const w of wants) { const i = n.indexOf(norm(w)); if (i >= 0) return i; }
    for (const w of wants) {
      const nw = norm(w);
      if (!nw) continue;
      for (let i = 0; i < n.length; i++) if (n[i] && (n[i].startsWith(nw) || nw.startsWith(n[i])) && Math.min(n[i].length, nw.length) >= 2) return i;
      const re = new RegExp(`(^| )${nw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}( |$)`);
      for (let i = 0; i < n.length; i++) if (nw.length >= 3 && re.test(n[i])) return i;
    }
    return -1;
  }

  // ---------- primitive actions that React/Vue apps notice ----------
  function setNative(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function press(el, key) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key, code: key, bubbles: true, cancelable: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key, code: key, bubbles: true }));
  }
  function realClick(el) {
    el.scrollIntoView({ block: "center" });
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    el.click();
  }
  const visibleOptions = () =>
    [...document.querySelectorAll('[role="option"], [data-automation-id="promptOption"], .dropdown-location, .pac-item')].filter(isVis);
  const optionText = (o) => (o.innerText || o.textContent || "").replace(/\s+/g, " ").trim();
  const PLACEHOLDER = /^(no options|no results|loading|searching|type to search|start typing)/i;

  async function settleOptions(ms = 3500) {
    let last = "", waited = 0, opts = [];
    while (waited < ms) {
      await sleep(300); waited += 300;
      opts = visibleOptions().filter((o) => !PLACEHOLDER.test(optionText(o)));
      const sig = opts.map(optionText).join("|");
      if (opts.length && sig === last) return opts;
      last = sig;
    }
    return opts;
  }

  async function openMenu(el) {
    el.focus();
    realClick(el);
    await sleep(250);
    if (!visibleOptions().length) { press(el, "ArrowDown"); await sleep(250); }
  }
  async function closeMenu(el) { press(el, "Escape"); el.blur(); await sleep(120); }

  async function probe(f) {
    const el = byId(f.id);
    if (!el) return [];
    await openMenu(el);
    const opts = visibleOptions().map(optionText).filter((t) => t && !PLACEHOLDER.test(t));
    await closeMenu(el);
    return opts.slice(0, 150);
  }

  async function fillCombo(el, value, strict) {
    const cands = (Array.isArray(value) ? value : [value]).filter(Boolean).map(String);
    const first = cands[0] || "";
    const tries = [first];
    const words = first.split(/[\s,]+/);
    if (words.length > 3) tries.push(words.slice(0, 3).join(" "));
    for (const q of tries) {
      el.focus();
      realClick(el);
      setNative(el, "");
      setNative(el, q.slice(0, 60));
      const opts = await settleOptions();
      let i = matchOption(cands, opts.map(optionText));
      if (i < 0 && !strict && opts.length) i = 0;
      if (i >= 0) { realClick(opts[i]); await sleep(300); return true; }
      await closeMenu(el);
    }
    return false;
  }

  async function fillListbox(el, value) {
    realClick(el);
    const opts = await settleOptions(2500);
    const i = matchOption(value, opts.map(optionText));
    if (i < 0) { press(el, "Escape"); return false; }
    realClick(opts[i]);
    await sleep(300);
    return true;
  }

  function checkMember(m) {
    if (m.checked) return true;
    const lab = (m.id && document.querySelector(`label[for="${CSS.escape(m.id)}"]`)) || m.closest("label");
    if (lab && isVis(lab)) lab.click(); else m.click();
    return m.checked;
  }

  async function fillFile(el, kind, appId) {
    const f = await send({ type: "file", kind, appId });
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], f.name, { type: f.mime }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(800);
    return { ok: true, shown: kind === "resume" ? "your uploaded resume" : f.name };
  }

  async function fillOne(f, a, appId) {
    if (a.file) return fillFile(byId(f.id), a.file, appId);
    const v = a.value;
    const first = Array.isArray(v) ? v[0] : v;
    const el = byId(f.id);
    switch (f.kind) {
      case "text":
      case "textarea": {
        if (!el) return { ok: false };
        el.focus(); setNative(el, String(first)); el.blur();
        if (el.value.trim()) return { ok: true, shown: String(first) };
        return { ok: await fillCombo(el, v, false), shown: String(first) };   // autocomplete that clears free text
      }
      case "select": {
        const opts = [...el.options];
        const i = matchOption(v, opts.map((o) => o.text.trim()));
        if (i < 0) return { ok: false, shown: String(first) };
        setNative(el, opts[i].value);
        return { ok: true, shown: opts[i].text.trim() };
      }
      case "radio":
      case "checkboxes": {
        const wants = f.kind === "radio" ? [matchOption(v, f.options)] : (Array.isArray(v) ? v : [v]).map((x) => matchOption(x, f.options));
        const idx = [...new Set(wants.filter((i) => i >= 0))];
        if (!idx.length) return { ok: false, shown: String(first) };
        let ok = true;
        for (const i of idx) { const m = byId(`${f.id}.${i}`); ok = !!m && checkMember(m) && ok; }
        return { ok, shown: idx.map((i) => f.options[i]).join(" | ") };
      }
      case "checkbox": {
        const want = ["checked", "true", "yes", "1"].includes(String(first).toLowerCase());
        if (el && el.checked !== want) checkMember(el);
        return { ok: !!el && el.checked === want, shown: want ? "checked" : "" };
      }
      case "buttons": {
        const i = matchOption(v, f.options);
        const b = i >= 0 && byId(`${f.id}.${i}`);
        if (!b) return { ok: false, shown: String(first) };
        realClick(b);
        return { ok: true, shown: f.options[i] };
      }
      case "combobox":
      case "multiselect":
        return { ok: !!el && (await fillCombo(el, v, !!(f.options && f.options.length) && f.kind === "combobox")), shown: String(first) };
      case "listbox":
        return { ok: !!el && (await fillListbox(el, v)), shown: String(first) };
      case "date": {
        const m = String(first).match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/) || [];
        const parts = { month: m[1], day: m[2], year: m[3] };
        let ok = true;
        (f.options || []).forEach((kind, i) => { const p = byId(`${f.id}.${i}`); if (p && parts[kind]) setNative(p, parts[kind].padStart(kind === "year" ? 4 : 2, "0")); else ok = false; });
        return { ok, shown: String(first) };
      }
    }
    return { ok: false };
  }

  async function waitForAutofill(maxMs = 25000) {
    const busy = /parsing|autofilling|auto-filling|analy[sz]ing|reading your resume|processing your resume|uploading/i;
    await sleep(1500);
    for (let t = 0; t < maxMs; t += 1000) {
      if (!busy.test(document.body.innerText || "")) break;
      await sleep(1000);
    }
    await sleep(1500);
  }

  // ---------- the small panel ----------
  let host, shadow;
  function panel(html, opts = {}) {
    if (!host) {
      host = document.createElement("div");
      host.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;";
      shadow = host.attachShadow({ mode: "open" });
      document.documentElement.appendChild(host);
    }
    shadow.innerHTML = `<style>
      .c{font:13px/1.45 "Segoe UI",system-ui,sans-serif;background:#fff;color:#15181d;border:1px solid #e4e7ec;border-radius:12px;box-shadow:0 8px 30px rgba(16,24,40,.18);width:320px;max-height:60vh;overflow:auto;padding:12px 14px}
      .h{display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:6px}.logo{width:18px;height:18px;border-radius:5px;background:#3b5bdb;display:grid;place-items:center}
      .x{margin-left:auto;border:0;background:none;font-size:16px;cursor:pointer;color:#9aa1ab}
      button.p{background:#3b5bdb;color:#fff;border:0;border-radius:8px;padding:8px 12px;font:inherit;font-weight:600;cursor:pointer;width:100%}
      ul{margin:6px 0 0;padding-left:18px}li{margin:2px 0}.m{color:#606874}.w{color:#b7600b}.g{color:#12805c}.b{color:#c92a2a}
      .spin{width:12px;height:12px;border:2px solid #d6defa;border-top-color:#3b5bdb;border-radius:50%;display:inline-block;animation:s .8s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
    </style><div class="c"><div class="h"><span class="logo"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 13 4 4L19 7"/></svg></span>AutoApply<button class="x" title="Close">×</button></div>${html}</div>`;
    shadow.querySelector(".x").onclick = () => { host.remove(); host = null; };
    if (opts.onFill) shadow.querySelector("button.p").onclick = opts.onFill;
  }

  // ---------- main flow ----------
  let running = false;
  async function run() {
    if (running) return;
    running = true;
    const records = new Map();
    const blanks = new Map();
    let appId = null;
    let resumeOnForm = false;
    try {
      panel(`<div class="m"><span class="spin"></span> Reading the form…</div>`);
      const handled = new Set();
      for (let round = 0; round < 4; round++) {
        const all = window.__autoapplyExtract();
        if (all.some((f) => f.kind === "file" && /resume|cv/i.test(f.question))) resumeOnForm = true;
        const todo = [];
        for (const f of all) {
          if (handled.has(f.id)) continue;
          if (f.current && (f.kind === "combobox" || f.kind === "select")) { todo.push(f); continue; }   // check site-filled choices
          if (f.current) {
            handled.add(f.id);
            if (f.kind !== "checkbox") records.set(f.question, { question: f.question, answer: f.kind === "file" ? "your uploaded resume" : f.current, source: "prefilled", required: f.required, kind: f.kind, ok: true });
            continue;
          }
          todo.push(f);
        }
        if (!todo.length) { if (round === 0 && !all.length) throw new Error("No application form on this part of the page."); break; }
        for (const f of todo) if ((f.kind === "combobox" || f.kind === "listbox") && !(f.options || []).length) f.options = await probe(f);
        panel(`<div class="m"><span class="spin"></span> Answering ${todo.length} question${todo.length === 1 ? "" : "s"}…</div>`);
        const res = await api("/api/ext/answer", { url: location.href, title: document.title, page_text: (document.body.innerText || "").slice(0, 12000), fields: todo, resume_on_form: resumeOnForm });
        appId = res.app_id;
        // The site's own resume reader goes first; then everything is read again.
        const auto = todo.filter((f) => res.answers[f.id] && res.answers[f.id].autofill);
        if (auto.length && round === 0) {
          for (const f of auto) {
            handled.add(f.id);
            panel(`<div class="m"><span class="spin"></span> Letting the site read your resume…</div>`);
            try { await fillFile(byId(f.id), "resume", appId); } catch { /* fall through to normal filling */ }
            await waitForAutofill();
          }
          continue;
        }
        let n = 0;
        for (const f of todo) {
          handled.add(f.id);
          const a = res.answers[f.id] || { value: null };
          if (a.keep) {
            records.set(f.question, { question: f.question, answer: String(f.current), source: "prefilled", required: f.required, kind: f.kind, ok: true });
            continue;
          }
          panel(`<div class="m"><span class="spin"></span> Filling ${++n} of ${todo.length}: ${f.question.slice(0, 60)}</div>`);
          let r = { ok: false, shown: "" };
          if (a.file || (a.value !== null && a.value !== undefined && a.value !== "")) {
            try { r = await fillOne(f, a, appId); } catch (e) { r = { ok: false, shown: "" }; }
          }
          if (f.kind === "checkbox" && !r.shown) continue;
          records.set(f.question, { question: f.question, answer: r.ok ? r.shown || "" : "", source: a.source || "", required: f.required, kind: f.kind, ok: r.ok });
          if (f.required && !r.ok) blanks.set(f.question, f.id); else blanks.delete(f.question);
        }
        await sleep(900);   // conditional questions appear after answers
      }
      for (const id of blanks.values()) {
        const el = byId(id) || byId(`${id}.0`);
        const box = el && (el.closest('[class*="field" i], fieldset, li') || el);
        if (box) { box.style.outline = "2px solid #f0a44b"; box.style.outlineOffset = "4px"; box.style.borderRadius = "6px"; }
      }
      await api("/api/ext/result", { app_id: appId, status: "filled", answers: [...records.values()] });
      const filled = [...records.values()].filter((r) => r.ok && r.answer).length;
      const left = [...blanks.keys()];
      panel(`<div class="g"><b>Filled ${filled} field${filled === 1 ? "" : "s"}.</b></div>` +
        (left.length ? `<div class="w" style="margin-top:6px"><b>${left.length} need you</b> (outlined in orange):<ul>${left.map((q) => `<li>${q.replace(/</g, "&lt;").slice(0, 110)}</li>`).join("")}</ul></div>` : "") +
        `<div class="m" style="margin-top:8px">Check it over, then press the form's Submit. AutoApply notices when it's sent and logs it.</div>`);
      watchForSubmit(appId);
    } catch (e) {
      panel(`<div class="b">${String(e.message || e).replace(/</g, "&lt;")}</div>`);
    } finally {
      running = false;
    }
  }

  function watchForSubmit(appId) {
    const re = window.__autoapplySuccess;
    const before = (document.body.innerText || "").slice(0, 20000);
    const startUrl = location.href;
    const t0 = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - t0 > 30 * 60 * 1000) return clearInterval(timer);
      const text = (document.body.innerText || "").slice(0, 20000);
      const urlDone = location.href !== startUrl && /\/(thanks|thank-you|thankyou|confirmation|submitted|success|applied)/i.test(location.href);
      if ((re.test(text) && !re.test(before)) || urlDone) {
        clearInterval(timer);
        try { await api("/api/ext/result", { app_id: appId, status: "sent" }); } catch { /* app offline; nothing to log to */ }
        panel(`<div class="g"><b>Sent.</b> Logged in AutoApply.</div>`);
      }
    }, 2000);
  }

  // ---------- entry points ----------
  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg && msg.type === "fill") {
      const n = window.__autoapplyExtract().filter((f) => f.kind !== "file" || f.question).length;
      if (n >= 2) { run(); reply({ ok: true, fields: n }); } else reply({ ok: false, fields: n });
    }
  });

  // On known application sites, offer a button once a real form is on the page.
  let offered = false;
  function maybeOffer() {
    if (offered || running) return;
    let fields = [];
    try { fields = window.__autoapplyExtract(); } catch { return; }
    if (fields.length < 3 || !fields.some((f) => /e-?mail|name|resume|cv/i.test(f.question))) return;
    offered = true;
    panel(`<div class="m" style="margin-bottom:8px">This looks like a job application (${fields.length} fields).</div><button class="p">Fill with AutoApply</button>`, { onFill: run });
  }
  setTimeout(maybeOffer, 1500);
  const mo = new MutationObserver(() => { clearTimeout(mo.t); mo.t = setTimeout(maybeOffer, 800); });
  mo.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(() => mo.disconnect(), 60000);
})();

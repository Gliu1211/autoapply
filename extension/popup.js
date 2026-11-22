const $ = (id) => document.getElementById(id);
const call = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: "no response" })));
const api = (path, body) => call({ type: "api", path, body, method: body ? "POST" : "GET" });
const status = (text, cls = "") => { $("status").textContent = text; $("status").className = `msg ${cls}`; };

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function load() {
  const r = await api("/api/state");
  if (!r.ok) { $("offline").hidden = false; $("online").hidden = true; return; }
  const s = r.data;
  $("offline").hidden = true; $("online").hidden = false;
  $("title").textContent = s.name ? `AutoApply · ${s.name}` : "AutoApply";
  $("today").textContent = `${s.stats.sent_today}`;
  $("week").textContent = `${s.stats.sent_7d}`;
  $("needs").textContent = `${s.stats.needs_you}`;
  $("auto").classList.toggle("on", !!s.autopilot.enabled);
  $("autoLabel").textContent = s.autopilot.enabled
    ? `Autopilot on · ${s.stats.sent_today} of ${s.autopilot.daily_cap} today`
    : "Autopilot off";
  if (s.missing && s.missing.length) {
    $("missing").hidden = false;
    $("missing").textContent = `Finish your profile first: ${s.missing.join(", ")}.`;
  }
}

$("auto").onclick = async () => {
  const on = !$("auto").classList.contains("on");
  const r = await api("/api/autopilot", { enabled: on });
  if (!r.ok) return status(r.error, "bad");
  load();
};

$("fill").onclick = async () => {
  const tab = await activeTab();
  if (!tab || !/^https?:/.test(tab.url || "")) return status("Open a job application page first.", "warn");
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["extract.js", "content.js"] });
  } catch (e) {
    return status(`Can't run on this page: ${e.message}`, "bad");
  }
  // Every frame gets the message; only the one holding the form answers ok.
  chrome.tabs.sendMessage(tab.id, { type: "fill" }, (r) => {
    if (chrome.runtime.lastError || !r) {
      status("Working on it in the page…", "good");
      setTimeout(() => window.close(), 900);
      return;
    }
    if (r.ok) { window.close(); return; }
    status("No application form found here. Click the job's Apply button first, then try again.", "warn");
  });
};

$("later").onclick = async () => {
  const tab = await activeTab();
  if (!tab || !/^https?:/.test(tab.url || "")) return status("Open a job posting first.", "warn");
  const r = await api("/api/jobs/add", { url: tab.url });
  if (!r.ok) return status(r.error, "bad");
  status("Added. Autopilot applies to it next.", "good");
  load();
};

$("dash").onclick = () => call({ type: "dashboard", path: "/#/applications" });

load();

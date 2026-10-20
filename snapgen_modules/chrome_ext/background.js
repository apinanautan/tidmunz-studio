// Tidmunz ChatGPT: runs Tidmunz Studio's ChatGPT jobs in this Chrome's own ChatGPT tab.
// Tidmunz (127.0.0.1) hands out one job at a time; this worker types the prompt, attaches
// reference pictures, waits for ChatGPT to finish and sends back the text / pictures.
// Pairing: a folder install gets port + token from config.js; the Chrome Web Store build
// asks Tidmunz for them at /pair (Tidmunz only answers requests coming from an extension).
try { importScripts("config.js"); } catch (e) { self.TZ_CONFIG = null; }

const PORTS = [8766, 8767, 8768];
const JOB_LIMIT_MS = 12 * 60 * 1000;
let running = false;
let link = self.TZ_CONFIG || null;  // {port, token}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function pair() {
  if (self.TZ_CONFIG) return self.TZ_CONFIG;
  for (const port of PORTS) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/pair`);
      if (response.ok) {
        const data = await response.json();
        if (data && data.token) return {port, token: data.token};
      }
    } catch (e) {}
  }
  return null;
}

async function relay(path, body) {
  if (!link) link = await pair();
  if (!link) throw new Error("Tidmunz is not open");
  const url = `http://127.0.0.1:${link.port}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(link.token)}`;
  const opts = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const response = await fetch(url, opts);
  if (response.status === 403 && !self.TZ_CONFIG) link = null;  // Tidmunz restarted with a new token: pair again
  if (response.status === 204) return null;
  if (!response.ok) throw new Error(`relay ${response.status}`);
  return response.json();
}

function badge(text, color) {
  try { chrome.action.setBadgeText({text}); chrome.action.setBadgeBackgroundColor({color}); } catch (e) {}
}

// Which ChatGPT account this Chrome profile is logged into and its picture quota (shown in Tidmunz).
let account = {email: "", images: "", reset: "", plan: "", used: "", checked: 0};
async function chatgptAccount() {
  if (Date.now() - account.checked < 120000) return account;
  const next = {email: "", images: "", reset: "", plan: "", used: "", checked: Date.now()};
  try {
    const session = await (await fetch("https://chatgpt.com/api/auth/session", {credentials: "include"})).json();
    next.email = (session && session.user && session.user.email) || "";
    next.plan = (session && session.account && (session.account.planType || session.account.plan_type)) || "";
    if (session && session.accessToken) {
      // The same call chatgpt.com makes when it opens: it reports what is left of each limit.
      const init = await (await fetch("https://chatgpt.com/backend-api/conversation/init", {
        method: "POST", credentials: "include",
        headers: {"Authorization": "Bearer " + session.accessToken, "Content-Type": "application/json"},
        body: JSON.stringify({requested_default_model: null, conversation_id: null,
                              timezone_offset_min: new Date().getTimezoneOffset(), conversation_origin: null})})).json();
      const image = (init.limits_progress || []).find(f => f.feature_name === "image_gen");
      const blocked = (init.blocked_features || []).find(f => f.name === "image_gen" || f.feature_name === "image_gen");
      if (blocked) { next.images = "0"; next.reset = blocked.resets_after || blocked.reset_after || ""; }
      else if (image) { next.images = String(image.remaining); next.reset = image.reset_after || ""; }
      // ChatGPT's "remaining" stays at the full number until the limit is near. The account's picture
      // library holds every picture made on it (from every PC), so it tells how many the last 24 hours took.
      try {
        const library = await (await fetch("https://chatgpt.com/backend-api/my/recent/image_gen?limit=200", {
          credentials: "include", headers: {"Authorization": "Bearer " + session.accessToken}})).json();
        const since = Date.now() / 1000 - 86400;
        if (Array.isArray(library.items)) next.used = String(library.items.filter(i => (i.created_at || 0) > since).length);
      } catch (e) {}
    }
  } catch (e) {}
  account = next;
  return account;
}

async function loop() {
  if (running) return;
  running = true;
  try {
    for (;;) {
      let job = null;
      try {
        const who = await chatgptAccount();
        job = await relay(`/next?wait=25&v=${chrome.runtime.getManifest().version}&email=${encodeURIComponent(who.email)}` +
                          `&images=${encodeURIComponent(who.images)}&reset=${encodeURIComponent(who.reset)}&plan=${encodeURIComponent(who.plan)}` +
                          `&used=${encodeURIComponent(who.used)}`);
        badge("ON", "#10A37F");
      } catch (e) {
        badge("OFF", "#9CA3AF");  // Tidmunz is not open: look again later
        await sleep(5000);
        continue;
      }
      if (!job) continue;
      if (job.reload) { chrome.runtime.reload(); return; }  // Tidmunz updated this folder: load the new files
      badge("…", "#F59E0B");
      let result;
      try {
        result = await runJob(job);
      } catch (e) {
        result = {error: String(e && e.message || e)};
      }
      try { await relay("/result", {id: job.id, ...result}); } catch (e) {}
      // Leave the (possibly very long) chat so the tab does not keep it in memory between jobs.
      try { const saved = (await chrome.storage.session.get("tabId")).tabId; if (saved) await chrome.tabs.update(saved, {url: "https://chatgpt.com/"}); } catch (e) {}
      if (job.type === "image") account.checked = 0;  // read the quota again after a picture
      badge("ON", "#10A37F");
    }
  } finally {
    running = false;
  }
}

// ── the ChatGPT tab ──
async function workerTab() {
  const saved = (await chrome.storage.session.get("tabId")).tabId;
  let id = null;
  if (saved) {
    try {
      const tab = await chrome.tabs.get(saved);
      if (tab && /^https:\/\/chatgpt\.com\//.test(tab.url || tab.pendingUrl || "")) id = tab.id;
    } catch (e) {}
  }
  if (id === null) {
    const tab = await chrome.tabs.create({url: "https://chatgpt.com/", active: false, pinned: true});
    await chrome.storage.session.set({tabId: tab.id});
    id = tab.id;
  }
  // Chrome's memory saver must not unload (and reload) the work tab in the middle of a job.
  try { await chrome.tabs.update(id, {autoDiscardable: false}); } catch (e) {}
  return id;
}

async function waitLoaded(tabId, limitMs) {
  const start = Date.now();
  for (;;) {
    await sleep(700);
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    if (Date.now() - start > limitMs) throw new Error("ChatGPT page did not load");
  }
}

async function openPage(tabId, url) {
  await chrome.tabs.update(tabId, {url});
  // Do not wait for "complete": ChatGPT keeps loading in the background; the composer is what counts.
  let seen = "", reloaded = false;
  const start = Date.now();
  while (Date.now() - start < 120000) {
    await sleep(1000);
    let ready = null;
    try {
      ready = await inPage(tabId, () => {
        const body = document.body ? document.body.innerText.slice(0, 300) : "";
        const page = (document.title + " | " + body).replace(/\s+/g, " ").slice(0, 160);
        if (/just a moment|verify you are human|ยืนยันว่าคุณเป็นมนุษย์|checking your browser/i.test(page)) return {state: "challenge", page};
        if (document.querySelector('form div.ProseMirror[role="textbox"]')) return {state: "ok", page};
        return {state: "", page};
      });
    } catch (e) {}  // still navigating
    if (ready && ready.state === "ok") return;
    if (ready && ready.page) seen = ready.page;
    if (ready && ready.state === "challenge")
      throw new Error("ChatGPT asks to confirm you are human — open the program's Chrome window and confirm, then try again");
    if (!reloaded && Date.now() - start > 45000) {  // stuck once: reload the tab and wait again
      reloaded = true;
      try { await chrome.tabs.reload(tabId); } catch (e) {}
    }
  }
  throw new Error("ChatGPT page not ready after 2 minutes — the window shows: " + (seen || "nothing"));
}

async function inPage(tabId, func, args) {
  const [{result}] = await chrome.scripting.executeScript({target: {tabId}, world: "MAIN", func, args: args || []});
  return result;
}

async function runJob(job) {
  const tabId = await workerTab();
  const url = job.conversation_id ? `https://chatgpt.com/c/${job.conversation_id}` : "https://chatgpt.com/";
  await openPage(tabId, url);
  // Messages already in an existing chat, read before sending: if the send's own answer is lost, a chat that
  // gained messages shows the job went out.
  let known = null;
  if (job.conversation_id) {
    try { known = await inPage(tabId, pageNodes, [job.conversation_id]); } catch (e) { known = null; }
  }
  let started = null;
  try {
    started = await inPage(tabId, pageSend, [job]);
  } catch (e) {
    started = null;  // the page moved to the new chat right after sending: its answer was lost, not the job
  }
  if (!started && !job.conversation_id) {
    // A long message (a whole story) is sent, then ChatGPT navigates to /c/<id> before the result comes back.
    // If a new chat opened, the message went out: keep waiting for the reply in that chat.
    for (let i = 0; i < 10 && !started; i++) {
      await sleep(1500);
      try {
        const tab = await chrome.tabs.get(tabId);
        if (/\/c\/[0-9a-f-]{20,}/.test(tab.url || "")) started = {ok: true, before: [], oldPointers: []};
      } catch (e) {}
    }
  }
  if (!started && job.conversation_id && Array.isArray(known)) {
    for (let i = 0; i < 10 && !started; i++) {
      await sleep(1500);
      try {
        const now = await inPage(tabId, pageNodes, [job.conversation_id]);
        if (Array.isArray(now) && now.length > known.length) started = {ok: true, before: known, oldPointers: []};
      } catch (e) {}
    }
  }
  if (!started || started.error) return started || {error: "ChatGPT page did not answer (no message reached the chat)"};
  // The job's state lives here, not in the page: a reloaded tab keeps going from the conversation itself.
  const state = {before: started.before, oldPointers: started.oldPointers, sentAt: Date.now(),
                 cid: job.conversation_id || null, idle: 0};
  const deadline = Date.now() + JOB_LIMIT_MS;
  while (Date.now() < deadline) {
    await sleep(3000);  // short calls keep this worker alive while ChatGPT works
    let result = null;
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status !== "complete") continue;  // reloading: look again when it is back
      result = await inPage(tabId, pagePoll, [job.type, state]);
    } catch (e) {
      continue;  // the page was navigating; the conversation keeps the answer
    }
    if (!result) continue;
    if (result.cid) state.cid = result.cid;
    if (result.idle != null) state.idle = result.idle;
    if (result.done || result.error) return result;
  }
  return {error: "ChatGPT took longer than 12 minutes"};
}

// ── functions below run inside the chatgpt.com page ──
async function pageNodes(cid) {
  const session = await (await fetch("/api/auth/session")).json();
  const r = await fetch(`/backend-api/conversation/${cid}`, {headers: {Authorization: "Bearer " + session.accessToken}});
  if (!r.ok) return null;
  return Object.keys((await r.json()).mapping || {});
}

async function pageSend(job) {
  try {
    return await pageSendInner(job);
  } catch (e) {
    return {error: "ChatGPT page error while sending: " + (e && e.message ? e.message : String(e))};
  }
  async function pageSendInner(job) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const session = await (await fetch("/api/auth/session")).json();
  if (!session || !session.accessToken) return {error: "ChatGPT is logged out in Chrome — log in, then try again"};
  let before = [];  // messages already in the chat: everything else is this job's answer
  const oldPointers = [];  // pictures already in the chat (earlier pictures, references): never the answer
  if (job.conversation_id) {
    const check = await fetch(`/backend-api/conversation/${job.conversation_id}`,
      {headers: {Authorization: "Bearer " + session.accessToken}});
    if (check.status === 404 || check.status === 403) return {error: "ChatGPT conversation failed: 404 — not found for this account", not_found: true};
    const data = await check.json();
    before = Object.keys(data.mapping || {});
    for (const node of Object.values(data.mapping || {}))
      for (const part of (node.message && node.message.content && node.message.content.parts) || [])
        if (part && typeof part === "object" && part.asset_pointer) oldPointers.push(part.asset_pointer);
  }
  const editor = document.querySelector('form div.ProseMirror[role="textbox"]');
  const form = editor.closest("form");
  const images = job.images || [];
  if (images.length) {
    const input = [...document.querySelectorAll('input[type=file]')].find(i => i.multiple && /image/.test(i.accept))
      || [...document.querySelectorAll('input[type=file]')].find(i => i.multiple);
    if (!input) return {error: "ChatGPT file input not found"};
    const dt = new DataTransfer();
    images.forEach((data, n) => {
      const [head, b64] = data.split(",");
      const mime = (head.match(/data:([^;]+)/) || [])[1] || "image/png";
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      dt.items.add(new File([bytes], `ref_${n + 1}.${mime.split("/")[1] || "png"}`, {type: mime}));
    });
    input.files = dt.files;
    input.dispatchEvent(new Event("change", {bubbles: true}));
  }
  editor.focus();
  document.execCommand("selectAll", false, null);
  document.execCommand("insertText", false, job.prompt);
  // Wait until every picture sits in the composer and has finished uploading. The send button is
  // enabled by the text alone, so pressing it early sent the words without the picture (an image
  // check then answered "all fine" about a sheet it never saw).
  const until = Date.now() + 120000;
  let send = null, attached = 0, settled = 0;
  while (Date.now() < until) {
    await sleep(800);
    attached = [...form.querySelectorAll("button")].filter(b =>
      /^(ลบ|remove)\s/i.test((b.getAttribute("aria-label") || "").trim())).length;
    const uploading = form.querySelector('[role="progressbar"], [aria-busy="true"], circle[stroke-dasharray]');
    send = form.querySelector('button[type="submit"]');
    const ready = send && !send.disabled && attached >= images.length && !uploading;
    settled = ready ? settled + 1 : 0;
    if (settled >= 2) break;  // ready twice in a row: the upload is really done
  }
  if (images.length && attached < images.length)
    return {error: `ChatGPT did not take the attached pictures (${attached}/${images.length} attached) — not sent`};
  if (!send || send.disabled) return {error: "ChatGPT did not accept the attachments / prompt in time"};
  send.click();
  return {ok: true, before, oldPointers};
  }
}

async function pagePoll(kind, state) {
  const busy = [...document.querySelectorAll("form button, button[data-testid]")].some(b =>
    b.getAttribute("data-testid") === "stop-button" ||
    /^(stop|หยุด|停止|detener|arrêter|stopp)/i.test((b.getAttribute("aria-label") || "").trim()));
  const match = location.pathname.match(/\/c\/([0-9a-f-]{20,})/);
  const cid = state.cid || (match ? match[1] : null);
  const idle = busy ? 0 : (state.idle || 0) + 1;
  const elapsed = (Date.now() - state.sentAt) / 1000;
  if (!cid || idle < 2 || elapsed < 6) return {done: false, cid, idle};

  const token = (await (await fetch("/api/auth/session")).json()).accessToken;
  const auth = {headers: {Authorization: "Bearer " + token}};
  const data = await (await fetch(`/backend-api/conversation/${cid}`, auth)).json();
  const nodes = Object.entries(data.mapping || {}).filter(([id, n]) => n.message && !state.before.includes(id))
    .map(([, n]) => n.message).sort((a, b) => (a.create_time || 0) - (b.create_time || 0));
  const skip = new Set(state.oldPointers || []);
  const pointers = [];
  let text = "";
  for (const m of nodes) {
    const role = m.author && m.author.role;
    const parts = (m.content && m.content.parts) || [];
    if (role === "user") {  // the pictures we attached come back in answers too
      for (const part of parts) if (part && typeof part === "object" && part.asset_pointer) skip.add(part.asset_pointer);
      continue;
    }
    for (const part of parts)
      if (part && typeof part === "object" && part.asset_pointer && !pointers.includes(part.asset_pointer)) pointers.push(part.asset_pointer);
    if (role === "assistant" && m.content && m.content.content_type === "text") {
      const t = parts.filter(p => typeof p === "string").join("\n").trim();
      if (t) text = t;
    }
    if (m.content && m.content.content_type === "system_error") text = (m.content.text || text);
  }
  const fresh = pointers.filter(p => !skip.has(p));
  // A picture can still be drawing after the reply text: give it time unless ChatGPT answered instead.
  if (kind === "image" && !fresh.length && !text && elapsed < 240) return {done: false, cid, idle};
  const images = [];
  for (const pointer of (kind === "image" ? fresh.slice(-1) : fresh)) {  // one picture per job: the newest
    const id = pointer.replace(/^[a-z-]+:\/\//, "");
    const info = await (await fetch(`/backend-api/files/download/${id}?conversation_id=${cid}&inline=false`, auth)).json();
    if (!info.download_url) continue;
    const blob = await (await fetch(info.download_url)).blob();
    images.push(await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); }));
  }
  return {done: true, text, images, conversation_id: cid, message_id: data.current_node || null};
}

chrome.alarms.create("tidmunz-wake", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => loop());
chrome.runtime.onStartup.addListener(() => loop());
chrome.runtime.onInstalled.addListener(() => loop());
loop();

// Tidmunz ChatGPT: runs Tidmunz Studio's ChatGPT jobs in this Chrome's own ChatGPT tab.
// Tidmunz (127.0.0.1, see config.js) hands out one job at a time; this worker types the prompt,
// attaches reference pictures, waits for ChatGPT to finish and sends back the text / pictures.
importScripts("config.js");

const BASE = `http://127.0.0.1:${self.TZ_CONFIG.port}`;
const TOKEN = self.TZ_CONFIG.token;
const JOB_LIMIT_MS = 12 * 60 * 1000;
let running = false;

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function relay(path, body) {
  const url = `${BASE}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}`;
  const opts = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const response = await fetch(url, opts);
  if (response.status === 204) return null;
  if (!response.ok) throw new Error(`relay ${response.status}`);
  return response.json();
}

function badge(text, color) {
  try { chrome.action.setBadgeText({text}); chrome.action.setBadgeBackgroundColor({color}); } catch (e) {}
}

async function loop() {
  if (running) return;
  running = true;
  try {
    for (;;) {
      let job = null;
      try {
        job = await relay(`/next?wait=25&v=${chrome.runtime.getManifest().version}`);
        badge("ON", "#10A37F");
      } catch (e) {
        badge("OFF", "#9CA3AF");  // Tidmunz is not open: look again later
        await sleep(5000);
        continue;
      }
      if (!job) continue;
      badge("…", "#F59E0B");
      let result;
      try {
        result = await runJob(job);
      } catch (e) {
        result = {error: String(e && e.message || e)};
      }
      try { await relay("/result", {id: job.id, ...result}); } catch (e) {}
      badge("ON", "#10A37F");
    }
  } finally {
    running = false;
  }
}

// ── the ChatGPT tab ──
async function workerTab() {
  const saved = (await chrome.storage.session.get("tabId")).tabId;
  if (saved) {
    try {
      const tab = await chrome.tabs.get(saved);
      if (tab && /^https:\/\/chatgpt\.com\//.test(tab.url || tab.pendingUrl || "")) return tab.id;
    } catch (e) {}
  }
  const tab = await chrome.tabs.create({url: "https://chatgpt.com/", active: false, pinned: true});
  await chrome.storage.session.set({tabId: tab.id});
  return tab.id;
}

async function openPage(tabId, url) {
  await chrome.tabs.update(tabId, {url});
  const start = Date.now();
  for (;;) {
    await sleep(700);
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") break;
    if (Date.now() - start > 60000) throw new Error("ChatGPT page did not load");
  }
  for (let i = 0; i < 60; i++) {  // wait for the composer
    const [{result}] = await chrome.scripting.executeScript({target: {tabId}, world: "MAIN",
      func: () => !!document.querySelector('form div.ProseMirror[role="textbox"]')});
    if (result) return;
    await sleep(1000);
  }
  throw new Error("ChatGPT composer not found (logged out or page changed)");
}

async function inPage(tabId, func, args) {
  const [{result}] = await chrome.scripting.executeScript({target: {tabId}, world: "MAIN", func, args: args || []});
  return result;
}

async function runJob(job) {
  const tabId = await workerTab();
  const url = job.conversation_id ? `https://chatgpt.com/c/${job.conversation_id}` : "https://chatgpt.com/";
  await openPage(tabId, url);
  const started = await inPage(tabId, pageSend, [job]);
  if (started && started.error) return started;
  const deadline = Date.now() + JOB_LIMIT_MS;
  while (Date.now() < deadline) {
    await sleep(3000);  // short calls keep this worker alive while ChatGPT works
    const state = await inPage(tabId, pagePoll, [job.type]);
    if (state && (state.done || state.error)) return state;
  }
  return {error: "ChatGPT took longer than 12 minutes"};
}

// ── functions below run inside the chatgpt.com page ──
async function pageSend(job) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const session = await (await fetch("/api/auth/session")).json();
  if (!session || !session.accessToken) return {error: "ChatGPT is logged out in Chrome — log in, then try again"};
  let before = [];  // messages already in the chat: everything else is this job's answer
  if (job.conversation_id) {
    const check = await fetch(`/backend-api/conversation/${job.conversation_id}`,
      {headers: {Authorization: "Bearer " + session.accessToken}});
    if (check.status === 404 || check.status === 403) return {error: "ChatGPT conversation failed: 404 — not found for this account", not_found: true};
    before = Object.keys((await check.json()).mapping || {});
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
  // Wait until the pictures are uploaded and ChatGPT lets us send.
  const until = Date.now() + 120000;
  let send = null;
  while (Date.now() < until) {
    await sleep(800);
    send = form.querySelector('button[type="submit"]');
    if (send && !send.disabled) break;
  }
  if (!send || send.disabled) return {error: "ChatGPT did not accept the attachments / prompt in time"};
  window.__tidmunz = {before, sentAt: Date.now(), cid: job.conversation_id || null, idle: 0};
  send.click();
  return {ok: true};
}

async function pagePoll(kind) {
  const state = window.__tidmunz;
  if (!state) return {error: "ChatGPT page reloaded while working"};
  const busy = [...document.querySelectorAll("form button, button[data-testid]")].some(b =>
    b.getAttribute("data-testid") === "stop-button" ||
    /^(stop|หยุด|停止|detener|arrêter|stopp)/i.test((b.getAttribute("aria-label") || "").trim()));
  const match = location.pathname.match(/\/c\/([0-9a-f-]{20,})/);
  if (match) state.cid = match[1];
  state.idle = busy ? 0 : state.idle + 1;
  const elapsed = (Date.now() - state.sentAt) / 1000;
  if (!state.cid || state.idle < 2 || elapsed < 6) return {done: false};

  const token = (await (await fetch("/api/auth/session")).json()).accessToken;
  const auth = {headers: {Authorization: "Bearer " + token}};
  const data = await (await fetch(`/backend-api/conversation/${state.cid}`, auth)).json();
  const nodes = Object.entries(data.mapping || {}).filter(([id, n]) => n.message && !state.before.includes(id))
    .map(([, n]) => n.message).sort((a, b) => (a.create_time || 0) - (b.create_time || 0));
  const pointers = [];
  let text = "";
  for (const m of nodes) {
    const role = m.author && m.author.role;
    if (role === "user") continue;
    for (const part of (m.content && m.content.parts) || []) {
      if (part && typeof part === "object" && part.asset_pointer && !pointers.includes(part.asset_pointer)) pointers.push(part.asset_pointer);
    }
    if (role === "assistant" && m.content && m.content.content_type === "text") {
      const t = (m.content.parts || []).filter(p => typeof p === "string").join("\n").trim();
      if (t) text = t;
    }
    if (m.content && m.content.content_type === "system_error") text = (m.content.text || text);
  }
  // A picture can still be drawing after the reply text: give it time unless ChatGPT answered instead.
  if (kind === "image" && !pointers.length && !text && elapsed < 240) return {done: false};
  const images = [];
  for (const pointer of pointers) {
    const id = pointer.replace(/^[a-z-]+:\/\//, "");
    const info = await (await fetch(`/backend-api/files/download/${id}?conversation_id=${state.cid}&inline=false`, auth)).json();
    if (!info.download_url) continue;
    const blob = await (await fetch(info.download_url)).blob();
    images.push(await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); }));
  }
  window.__tidmunz = null;
  return {done: true, text, images, conversation_id: state.cid, message_id: data.current_node || null};
}

chrome.alarms.create("tidmunz-wake", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => loop());
chrome.runtime.onStartup.addListener(() => loop());
chrome.runtime.onInstalled.addListener(() => loop());
loop();

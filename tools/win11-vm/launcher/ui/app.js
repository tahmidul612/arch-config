// vm-launch page. Talks to the Rust side through Tauri's invoke/events; opened in a plain browser it uses a mock
// backend (handy for working on the design: ?vm=running&mode=hdmi, ?launch to play a launch).
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const setText = (el, t) => { if (el.textContent !== t) el.textContent = t; };
const setProp = (el, k, v) => { if (el[k] !== v) el[k] = v; };
const setClass = (el, c) => { if (el.className !== c) el.className = c; };
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
gsap.defaults({ duration: reduce ? 0 : 0.22, ease: "power3.out" });

/* ------------------------------------------------------------------ backend */
const T = window.__TAURI__;
const backend = T ? {
  invoke: (c, a) => T.core.invoke(c, a),
  listen: (e, f) => T.event.listen(e, (ev) => f(ev.payload)),
} : mockBackend();

/* ------------------------------------------------------------------ prefs */
const DEFAULTS = { display: "lg", picture: "fsr", remote_play: true, ds4: true, switch_input: true, record: false, close_after: false };
let prefs = { ...DEFAULTS, ...JSON.parse(localStorage.getItem("vm-launch") || "{}") };
const save = () => localStorage.setItem("vm-launch", JSON.stringify(prefs));

let live = null;        // last get_state()
let phase = "idle";     // idle | launching | action
let firstState = true;

/* ------------------------------------------------------------------ segmented controls */
function renderSeg(seg, animate = true) {
  const key = seg.dataset.key, val = prefs[key];
  const btns = $$("button", seg), i = Math.max(0, btns.findIndex((b) => b.dataset.value === val));
  btns.forEach((b, j) => { b.setAttribute("aria-checked", String(j === i)); b.tabIndex = j === i ? 0 : -1; });
  gsap.to($(".thumb", seg), { xPercent: i * 100, duration: animate && !reduce ? 0.26 : 0 });
}
$$(".seg").forEach((seg) => {
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || seg.classList.contains("disabled")) return;
    choose(seg.dataset.key, b.dataset.value);
  });
  seg.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key) || seg.classList.contains("disabled")) return;
    e.preventDefault();
    const btns = $$("button", seg), i = btns.findIndex((b) => b.dataset.value === prefs[seg.dataset.key]);
    const n = btns[(i + (e.key === "ArrowRight" ? 1 : btns.length - 1)) % btns.length];
    choose(seg.dataset.key, n.dataset.value); n.focus();
  });
});
function choose(key, value) {
  if (prefs[key] === value) return;
  prefs[key] = value; save(); renderAll();
}

/* ------------------------------------------------------------------ toggles */
$$(".tg").forEach((tg) => {
  const input = $("input", tg);
  input.addEventListener("change", () => { prefs[tg.dataset.key] = input.checked; save(); renderAll(); });
});
function renderToggles() {
  const hdmi = prefs.display === "hdmi";
  const dis = { switch_input: !hdmi, record: hdmi };
  $$(".tg").forEach((tg) => {
    const key = tg.dataset.key, input = $("input", tg), small = $("small", tg);
    input.checked = !!prefs[key];
    const off = !!dis[key] || phase !== "idle";
    input.disabled = off; tg.classList.toggle("disabled", !!dis[key]);
    if (small.dataset.why) {
      small.dataset.base ??= small.textContent;
      small.textContent = dis[key] ? small.dataset.why : small.dataset.base;
    }
  });
}

/* ------------------------------------------------------------------ signal path */
const PATHS = {
  lg_fsr:    { mid: "Looking Glass", s1: "1920×1080", s2: "FSR 1 → 2160p", s3: "DisplayPort",
               note: "Windows renders 1080p; the host client upscales it with FSR 1." },
  lg_native: { mid: "Looking Glass", s1: "3840×2160", s2: "1:1, no filters", s3: "DisplayPort",
               note: "Windows renders 4K at 150%; the client shows its pixels untouched." },
  hdmi:      { mid: "iGPU HDMI", s1: "3840×2160", s2: "direct", s3: "HDMI-1",
               note: "The LG takes the iGPU's HDMI output; Deskflow carries keyboard and mouse." },
};
let pathKey = null;
function renderPath() {
  const key = prefs.display === "hdmi" ? "hdmi" : `lg_${prefs.picture}`;
  const p = PATHS[key];
  const note = prefs.display === "hdmi" && !prefs.switch_input
    ? "Windows goes to HDMI; the LG stays on the host until you switch it with its joystick."
    : p.note;
  if (key !== pathKey) {
    const changed = ["n2lab", "s1", "s2", "s3"].filter((id) => $("#" + id).textContent !== ({ n2lab: p.mid, s1: p.s1, s2: p.s2, s3: p.s3 })[id]);
    const els = changed.map((id) => "#" + id);
    if (pathKey && els.length && !reduce) {
      gsap.timeline()
        .to(els, { autoAlpha: 0, y: -4, duration: 0.12, ease: "power2.in" })
        .add(() => setPathText(p))
        .fromTo(els, { y: 4 }, { autoAlpha: 1, y: 0, duration: 0.22 });
      // redraw the wires from the source when the route changes
      gsap.fromTo(["#w1on", "#w2on"], { strokeDashoffset: 96 }, { strokeDashoffset: 0, duration: 0.42, stagger: 0.12, ease: "power2.out" });
    } else setPathText(p);
    pathKey = key;
  }
  $("#pathnote").textContent = note;
  renderPathLive();
}
function setPathText(p) { $("#n2lab").textContent = p.mid; $("#s1").textContent = p.s1; $("#s2").textContent = p.s2; $("#s3").textContent = p.s3; }
// wires light up when the VM runs in the chosen mode; dim otherwise
let litNow = null;
function renderPathLive() {
  const on = !!(live && live.vm === "running" && live.mode === prefs.display && (prefs.display === "hdmi" || live.picture === prefs.picture));
  const lit = phase === "launching" || on;
  if (lit !== litNow) { gsap.to(["#w1on", "#w2on"], { opacity: lit ? 1 : 0, duration: litNow === null ? 0 : 0.25 }); litNow = lit; }
  $$(".node").forEach((n) => n.classList.toggle("live", on));
}
$$("#w1on, #w2on").forEach((w) => { w.style.strokeDasharray = "96"; w.style.strokeDashoffset = "0"; w.style.opacity = "0"; });

// launch: a pulse travels Windows -> LG while steps run
let pulseTl = null;
function pulse(on) {
  if (reduce) return;
  pulseTl?.kill(); pulseTl = null;
  if (!on) { gsap.to("#pulse", { opacity: 0, duration: 0.2 }); return; }
  pulseTl = gsap.timeline({ repeat: -1, repeatDelay: 0.25, defaults: { ease: "none" } })
    .set("#pulse", { attr: { cx: 92 }, opacity: 0 })
    .to("#pulse", { opacity: 1, duration: 0.12 })
    .to("#pulse", { attr: { cx: 188 }, duration: 0.45 }, "<")
    .set("#pulse", { opacity: 0 })
    .set("#pulse", { attr: { cx: 332 } })
    .to("#pulse", { opacity: 1, duration: 0.1 }, "+=0.18")
    .to("#pulse", { attr: { cx: 428 }, duration: 0.45 }, "<")
    .to("#pulse", { opacity: 0, duration: 0.12 });
}

/* ------------------------------------------------------------------ live state panel */
// minute resolution: the panel refreshes every 2 s, so seconds would jump in steps of two
const upSince = (t) => t == null ? null : Math.max(0, Math.floor(Date.now() / 1000) - t);
const fmtUp = (s) => s == null ? "" : s < 60 ? "<1m" : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${String(Math.floor(s / 60) % 60).padStart(2, "0")}m`;
function statRows(s) {
  const run = s.vm === "running";
  const rp = s.ds4_service ? (s.ds4_attached ? ["open", "on"] : ["closed", ""]) : ["unknown", ""];
  const res = s.mode === "hdmi" || s.picture === "native" ? "3840×2160" : "1920×1080";
  return [
    ["Display", run ? (s.mode === "hdmi" ? "Direct HDMI" : "Looking Glass") : "", "", !run],
    ["Windows", run ? res : "off", "", !run],
    ["LG input", s.lg_input ? (s.lg_input === "dp" ? "DP · host" : s.lg_input === "hdmi" ? "HDMI · VM" : s.lg_input) : "…", s.lg_input === "hdmi" ? "on" : "", false],
    ["Looking Glass", s.client ? "open" : "closed", s.client ? "on" : "", !run],
    ["Remote Play", run ? rp[0] : "", rp[1], !run],
    ["DualShock 4", !s.ds4_service ? "service off" : s.ds4_attached ? "attached" : "waiting", s.ds4_attached ? "on" : s.ds4_service ? "" : "warn", false],
    ["Recording", s.recording ? "recording" : s.obs ? "OBS idle" : "off", s.recording ? "bad" : "", false],
  ];
}
function renderStats() {
  if (!live) return;
  const ul = $("#stats");
  const rows = statRows(live);
  if (ul.children.length !== rows.length) ul.innerHTML = rows.map(() => `<li><span class="k"></span><span class="v"><i class="dot"></i><b></b></span></li>`).join("");
  rows.forEach(([k, v, dot, dim], i) => {
    const li = ul.children[i];
    li.classList.toggle("dim", dim);
    setText($(".k", li), k);
    const b = $("b", li); b.style.fontWeight = "inherit";
    const shown = v || "—";
    if (b.textContent !== shown) {
      if (!firstState && !reduce) gsap.fromTo(b, { autoAlpha: 0.2 }, { autoAlpha: 1, duration: 0.3 });
      b.textContent = shown;
    }
    const d = $(".dot", li); setClass(d, "dot " + dot); d.hidden = !dot;
  });
}
function renderChip() {
  const run = live?.vm === "running";
  const dot = $("#vmchip .dot");
  setClass(dot, "dot " + (phase === "launching" || phase === "stopping" ? "warn" : run ? "on" : ""));
  setText($("#vmtext"), phase === "launching" ? "launching" : phase === "stopping" ? "shutting down" : !live ? "checking" : run ? `running · ${fmtUp(upSince(live.started_at))}` : "off");
}
function renderButtons() {
  const run = live?.vm === "running", idle = phase === "idle";
  const go = $("#go");
  setProp(go, "disabled", !idle || !live);
  setText($("#golabel"), phase === "launching" ? "Launching" : run ? "Apply" : "Launch");
  setProp($("#host"), "disabled", !idle || !run || (live.mode === "lg" && !live.client && live.lg_input !== "hdmi"));
  setProp($("#shutdown"), "disabled", !idle || !run);
  const lgOnly = prefs.display === "hdmi";
  const picSeg = $('.seg[data-key="picture"]');
  picSeg.classList.toggle("disabled", lgOnly);
  $$("button", picSeg).forEach((b) => setProp(b, "disabled", lgOnly || !idle));
  $$('.seg[data-key="display"] button').forEach((b) => setProp(b, "disabled", !idle));
  picSeg.title = lgOnly ? "Direct HDMI always runs Windows at native 3840×2160" : "";
}
function renderAll(animate = true) {
  $$(".seg").forEach((s) => renderSeg(s, animate));
  renderToggles(); renderPath(); renderButtons(); renderChip();
}

// The backend pushes a "state" event whenever something changes (no polling here).
let stopTimer = null;
function applyState(s) {
  if (!s || !s.vm) return;            // the watcher has not looked yet
  live = s;
  // VM already running when the window opens: start from what it is doing now
  if (firstState && live.vm === "running") {
    prefs.display = live.mode; if (live.mode === "lg") prefs.picture = live.picture;
    renderAll(false);
  }
  if (phase === "stopping" && live.vm === "off") {
    phase = "idle"; clearTimeout(stopTimer); msg("Windows is off"); renderAll();
  }
  renderStats(); renderChip(); renderButtons(); renderPathLive();
  firstState = false;
}
async function poll() {
  try { applyState(await backend.invoke("get_state")); } catch (e) { msg(String(e), true); }
}

/* ------------------------------------------------------------------ messages */
function msg(text, bad = false) {
  const m = $("#msg");
  m.classList.toggle("bad", bad);
  if (m.textContent === text) return;
  gsap.timeline().to(m, { autoAlpha: 0, duration: 0.1 }).add(() => { m.textContent = text; }).to(m, { autoAlpha: 1, duration: 0.2 });
}

/* ------------------------------------------------------------------ launch steps */
const STEP_LABEL = {
  vm: "Start the VM", agent: "Guest agent", login: "Windows login", ds4: "Virtual DualShock 4",
  input: { lg: "LG on DisplayPort", hdmi: "LG to HDMI" }, layout: { lg: "Windows display", hdmi: "Windows on HDMI" },
  client: "Looking Glass client", deskflow: "Deskflow (keyboard, mouse)", rp: "PS Remote Play", rec: "OBS recording",
};
const DETAIL = { lgonly: "1920×1080 @100%", lgonly4k: "3840×2160 @150%", hdmi: "3840×2160 @150%", fsr: "fullscreen, FSR 1", native: "fullscreen, no filters" };
function plan(o) {
  const ids = ["vm", "agent", "login", "ds4"];
  ids.push(...(o.display === "hdmi" ? ["layout", "deskflow", "input"] : ["input", "layout", "client"]));
  ids.push("rp", "rec");
  return ids;
}
const ICON = `<svg class="spin" viewBox="0 0 18 18"><circle class="ring" cx="9" cy="9" r="7.25"/><path class="arc" d="M9 1.75 A7.25 7.25 0 0 1 16.25 9"/></svg><span class="disc"><svg viewBox="0 0 18 18"><path class="mark" d=""/></svg></span>`;
const MARK = { ok: "M5.4 9.3 L7.8 11.6 L12.6 6.6", fail: "M6.2 6.2 L11.8 11.8 M11.8 6.2 L6.2 11.8", skip: "M5.8 9 H12.2" };
let spinners = {};
function showSteps(opts) {
  const ol = $("#steps");
  ol.innerHTML = plan(opts).map((id) => {
    const l = STEP_LABEL[id]; const label = typeof l === "string" ? l : l[opts.display];
    return `<li data-id="${id}" class="pending"><span class="ic">${ICON}</span><span><span class="t">${label}</span><span class="d"></span></span></li>`;
  }).join("");
  $("#sidelabel").textContent = "Launch";
  const from = $("#stats"), to = ol;
  gsap.set(to, { autoAlpha: 1, x: 0 });   // a previous showStats() faded the list itself out
  gsap.timeline()
    .to(from, { autoAlpha: 0, x: -8, duration: 0.14, ease: "power2.in" })
    .add(() => { from.hidden = true; to.hidden = false; })
    .fromTo($$("li", to), { autoAlpha: 0, x: 8 }, { autoAlpha: 1, x: 0, stagger: reduce ? 0 : 0.03, duration: 0.22 });
}
function showStats() {
  const from = $("#steps"), to = $("#stats");
  $("#sidelabel").textContent = "Now";
  gsap.timeline()
    .to(from, { autoAlpha: 0, x: -8, duration: 0.14, ease: "power2.in" })
    .add(() => { from.hidden = true; to.hidden = false; })
    .fromTo(to, { autoAlpha: 0, x: 8 }, { autoAlpha: 1, x: 0, duration: 0.22 });
}
function onStep({ id, state, detail }) {
  const li = $(`#steps li[data-id="${id}"]`); if (!li) return;
  li.className = state;
  const d = $(".d", li); d.textContent = DETAIL[detail] || detail || "";
  const arc = $(".arc", li), spin = $(".spin", li), disc = $(".disc", li), mark = $(".mark", li);
  spinners[id]?.kill(); delete spinners[id];
  if (state === "run") {
    gsap.set(arc, { opacity: 1 });
    if (!reduce) spinners[id] = gsap.fromTo(spin, { rotation: 0 }, { rotation: 360, repeat: -1, duration: 0.9, ease: "none" });
    if (d.textContent) gsap.from(d, { autoAlpha: 0, y: -3 });
    msg(`${$(".t", li).textContent}…`);
    return;
  }
  gsap.set(arc, { opacity: 0 }); gsap.set(spin, { rotation: 0 });
  mark.setAttribute("d", MARK[state] || "");
  const len = 20;
  gsap.timeline()
    .fromTo(disc, { scale: 0 }, { scale: 1, duration: 0.2, ease: "power3.out" })
    .fromTo(mark, { strokeDasharray: len, strokeDashoffset: len }, { strokeDashoffset: 0, duration: 0.22, ease: "power2.out" }, "-=0.08");
  if (state === "fail") { gsap.fromTo(li, { x: -3 }, { x: 0, duration: 0.3, ease: "power2.out" }); }
}

async function launch() {
  if (phase !== "idle" || !live) return;
  const opts = { display: prefs.display, picture: prefs.picture, switch_input: prefs.switch_input, remote_play: prefs.remote_play, ds4: prefs.ds4, record: prefs.record };
  phase = "launching"; renderAll(); showSteps(opts); pulse(true);
  try { await backend.invoke("launch", { opts }); }
  catch (e) { phase = "idle"; pulse(false); showStats(); renderAll(); msg(String(e), true); }
}
async function finished({ ok, action }) {
  if (phase === "launching") {
    pulse(false); phase = "idle";
    const failed = $$("#steps li.fail .t").map((t) => t.textContent);
    msg(ok ? "Ready." : `Stopped at: ${failed[0] || "a step"}`, !ok);
    renderAll();
    if (ok && prefs.close_after) { setTimeout(() => backend.invoke("quit"), 1400); return; }
    setTimeout(() => { if (phase === "idle") showStats(); }, ok ? 2600 : 8000);
    return;
  }
  if (action === "shutdown" && ok && live?.vm === "running") {
    // the request was sent; stay in "shutting down" until the backend reports the VM off
    phase = "stopping"; msg("Shutting Windows down…"); renderAll();
    clearTimeout(stopTimer);
    stopTimer = setTimeout(() => { if (phase === "stopping") msg("Still shutting down; Windows may be installing updates"); }, 180000);
    return;
  }
  phase = "idle";
  msg(ok ? ({ host: "LG is back on the host", shutdown: "Windows is off", client: "Looking Glass reopened" })[action] || "Done" : `${action} failed`, !ok);
  renderAll();
}
async function act(name, text) {
  if (phase !== "idle") return;
  phase = "action"; renderAll(); msg(text);
  try { await backend.invoke("action", { name }); } catch (e) { phase = "idle"; renderAll(); msg(String(e), true); }
}

/* ------------------------------------------------------------------ buttons */
$("#go").addEventListener("click", launch);
$("#host").addEventListener("click", () => act("host", "Handing the LG back to the host…"));
// hold-to-confirm shutdown (pointer or Space/Enter held)
(() => {
  const b = $("#shutdown"), fill = $(".fill", b), txt = $(".txt", b);
  let tw = null;
  const start = () => {
    if (b.disabled || tw) return;
    txt.textContent = "Keep holding…";
    tw = gsap.fromTo(fill, { scaleX: 0 }, { scaleX: 1, duration: 0.9, ease: "none", onComplete: () => {
      tw = null; gsap.set(fill, { scaleX: 0 }); txt.textContent = "Hold to shut down";
      act("shutdown", "Shutting Windows down…");
    } });
  };
  const cancel = () => { if (!tw) return; tw.kill(); tw = null; gsap.to(fill, { scaleX: 0, duration: 0.18 }); txt.textContent = "Hold to shut down"; };
  b.addEventListener("pointerdown", start);
  ["pointerup", "pointerleave", "pointercancel"].forEach((e) => b.addEventListener(e, cancel));
  b.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); start(); } });
  b.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") cancel(); });
})();
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.target.closest("#shutdown, #host, .tg")) { e.preventDefault(); launch(); }
  if (e.key === "Escape" && phase === "idle") backend.invoke("quit");
});

/* ------------------------------------------------------------------ start */
backend.listen("step", onStep);
backend.listen("finished", finished);
backend.listen("state", applyState);
renderAll(false);
backend.invoke("config").then(async (c) => {
  $("#dry").hidden = !c.dry_run;
  await poll();
  if (c.go) launch();
}).catch((e) => msg(String(e), true));
// uptime is counted here from the VM's start time; the text changes once a minute
setInterval(() => { if (live?.vm === "running" && !document.hidden) renderChip(); }, 30000);

/* ------------------------------------------------------------------ browser mock */
function mockBackend() {
  const q = new URLSearchParams(location.search);
  const h = {};
  const st = { vm: q.get("vm") || "off", started_at: Math.floor(Date.now() / 1000) - 754, mode: q.get("mode") || "lg", picture: q.get("picture") || "fsr", client: q.get("vm") === "running", ds4_service: true, ds4_attached: q.get("vm") === "running", obs: false, lg_input: "dp", recording: false, busy: false };
  const emit = (e, p) => (h[e] || []).forEach((f) => f(p));
  const api = {
    get_state: () => ({ ...st }), config: () => ({ dry_run: q.has("dry"), vm: "win11" }), quit: () => {},
    launch: ({ opts }) => {
      const ids = plan(opts); let t = 0;
      ids.forEach((id) => {
        const skip = (id === "rec" && !opts.record) || (id === "rp" && !opts.remote_play);
        setTimeout(() => emit("step", { id, state: skip ? "skip" : "run", detail: id === "layout" ? (opts.display === "hdmi" ? "hdmi" : opts.picture === "native" ? "lgonly4k" : "lgonly") : "" }), t);
        if (!skip) setTimeout(() => emit("step", { id, state: q.get("fail") === id ? "fail" : "ok", detail: q.get("fail") === id ? "see the log" : id === "client" ? opts.picture : "" }), (t += 700));
        t += 120;
      });
      setTimeout(() => { Object.assign(st, { vm: "running", started_at: Math.floor(Date.now() / 1000), mode: opts.display, picture: opts.picture, client: opts.display === "lg" }); emit("state", { ...st }); emit("finished", { ok: !q.get("fail"), action: "launch" }); }, t + 200);
    },
    action: ({ name }) => {
      setTimeout(() => emit("finished", { ok: true, action: name }), 600);
      if (name === "shutdown") setTimeout(() => { Object.assign(st, { vm: "off", started_at: null, client: false, ds4_attached: false }); emit("state", { ...st }); }, 4000);
    },
  };
  if (q.has("launch")) setTimeout(() => launch(), 400);
  return { invoke: async (c, a) => api[c](a || {}), listen: (e, f) => ((h[e] ||= []).push(f), Promise.resolve()) };
}

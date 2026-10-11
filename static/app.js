/* MaintTriage frontend — simple guided flow: Home → Report → Result → Decide. */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let META = null, SERVER_AI = null, currentReport = null, currentWO = null, woFilter = "", allReportsCache = [];
let repFilter = "", repUrg = "", repSort = "new";

async function api(path, opts = {}) {
  const headers = Object.assign({"Content-Type": "application/json"}, opts.headers || {});
  const r = await fetch(path, Object.assign({}, opts, {headers}));
  const j = await r.json().catch(() => ({ok: false, error: "Bad server response"}));
  if (!r.ok && j.ok === undefined) j.ok = false;
  return {status: r.status, data: j};
}
function toast(msg, cls = "") {
  const d = document.createElement("div");
  d.className = "toast " + cls; d.textContent = msg;
  $("toasts").appendChild(d); setTimeout(() => d.remove(), 4200);
}
function timeAgo(ts) {
  const s = Math.max(0, (Date.now() / 1000 - (ts || 0)) | 0);
  if (s < 60) return "just now";
  if (s < 3600) return `${(s / 60) | 0} min ago`;
  if (s < 86400) return `${(s / 3600) | 0} h ago`;
  return `${(s / 86400) | 0} days ago`;
}
function priCls(p) {
  p = p || "";
  if (p.startsWith("P1")) return "danger";
  if (p.startsWith("P2")) return "warn";
  if (p.startsWith("P3")) return "info";
  return "ok";
}
/* Whole-row toggling: clicking anywhere on a checklist row ticks the box.
   Skips disabled boxes (decided jobs) and form controls inside the row. */
function wireRowToggle(listEl) {
  if (!listEl) return;
  listEl.querySelectorAll("li.chk").forEach((li) => {
    const box = li.querySelector('input[type="checkbox"]');
    if (!box || box.disabled) return;
    li.classList.add("rowclick");
    li.addEventListener("click", (e) => {
      if (e.target.closest("input,button,a,textarea,select")) return;
      box.click(); // native toggle + fires the persistence change-handler
    });
  });
}

/* ---------- navigation ---------- */
const TITLES = {
  home: ["Home", "What would you like to do?"],
  report: ["Report a problem", "Three short parts — about 2 minutes."],
  triage: ["Analysis result", "What the helper found. A person still decides."],
  reports: ["Reports", "Every problem in one place — newest first."],
  workorders: ["Work orders", "You decide: change it, approve it, or reject it."],
  manuals: ["Manuals", "Search fixes by symptom word."],
};
function goto(view) {
  document.querySelectorAll("#mainNav button").forEach((b) => {
    const on = b.dataset.view === view;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $("view-" + view).classList.add("active");
  $("pageTitle").textContent = TITLES[view][0]; $("pageSub").textContent = TITLES[view][1];
  window.scrollTo({top: 0});
  if (view === "home") loadDashboard();
  if (view === "report" && (!$("fId")?.value.trim() && !($("fIssue")?.value.trim()))) setWizardStep(1);
  if (view === "reports") loadReports();
  if (view === "workorders") loadWOs();
  if (view === "manuals") searchKB();
}
document.querySelectorAll("#mainNav button").forEach((b) => b.addEventListener("click", () => goto(b.dataset.view)));
document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => goto(b.dataset.goto)));

/* ---------- machine metadata & visual showcase ---------- */
const MACHINE_META = {
  "Centrifugal Pump": {
    img: "img/centrifugal_pump.jpg",
    desc: "Fluid transport pump with rotating impeller and mechanical seal assembly.",
    tag: "Pumps & Hydraulics",
    placeholderId: "PUMP-101",
    ico: "💧"
  },
  "Air Compressor": {
    img: "img/air_compressor.jpg",
    desc: "Rotary screw / reciprocating air compressor with pressure receiver tank.",
    tag: "Pneumatics & Air",
    placeholderId: "COMP-201",
    ico: "💨"
  },
  "HVAC Unit": {
    img: "img/hvac_unit.jpg",
    desc: "Commercial air handler / chiller unit with scroll compressor and blower fan.",
    tag: "Climate & Cooling",
    placeholderId: "HVAC-301",
    ico: "❄️"
  },
  "Conveyor Belt": {
    img: "img/conveyor_belt.jpg",
    desc: "Motorized material transport belt with drive pulleys, rollers, and tensioner.",
    tag: "Material Handling",
    placeholderId: "CONV-401",
    ico: "📦"
  },
  "Diesel Generator": {
    img: "img/diesel_generator.jpg",
    desc: "Backup/prime power genset with diesel combustion engine and alternator.",
    tag: "Power Generation",
    placeholderId: "GEN-501",
    ico: "⚡"
  },
  "Industrial Fan": {
    img: "img/industrial_fan.jpg",
    desc: "Heavy-duty ventilation / exhaust fan with direct or belt-driven impeller.",
    tag: "Ventilation",
    placeholderId: "FAN-601",
    ico: "🌀"
  }
};

let currentWizardStep = 1;

function updateMachinePreview(type) {
  const m = MACHINE_META[type] || MACHINE_META["Centrifugal Pump"];
  if ($("machineImg")) $("machineImg").src = m.img;
  if ($("machineTag")) $("machineTag").textContent = m.tag;
  if ($("machineTitle")) $("machineTitle").textContent = type;
  if ($("machineDesc")) $("machineDesc").textContent = m.desc;
  if ($("reviewImg")) $("reviewImg").src = m.img;
  if ($("reviewTag")) $("reviewTag").textContent = m.tag;
  if ($("reviewMachineName")) $("reviewMachineName").textContent = type;
  if ($("s2BadgeText")) $("s2BadgeText").textContent = `${type} · ${$("fId")?.value.trim() || m.placeholderId}`;
  if ($("fType") && $("fType").value !== type) $("fType").value = type;

  const currentId = ($("fId")?.value || "").trim();
  if (!currentId && $("fId")) {
    $("fId").placeholder = m.placeholderId;
  }
  document.querySelectorAll("#machineTiles .machine-tile").forEach((b) => {
    b.classList.toggle("active", b.dataset.mtype === type);
  });
}

/* ---------- multi-step wizard navigation ---------- */
function validateStep(step) {
  $("formErr").textContent = "";
  if (step === 1) {
    const id = ($("fId")?.value || "").trim();
    if (!id) {
      $("formErr").textContent = "Please enter the machine number (e.g. PUMP-101).";
      $("fId")?.focus();
      return false;
    }
  } else if (step === 2) {
    const issue = ($("fIssue")?.value || "").trim();
    const events = ($("fEvents")?.value || "").trim();
    if (issue.length < 10) {
      $("formErr").textContent = "Please describe the problem in a few more words (min 10 characters).";
      $("fIssue")?.focus();
      return false;
    }
    if (events.length < 3) {
      $("formErr").textContent = "Please tell us what happened recently — or write “none observed”.";
      $("fEvents")?.focus();
      return false;
    }
  } else if (step === 3) {
    const sensors = sensorsFromForm();
    for (const [k, v] of Object.entries(sensors)) {
      if (isNaN(v)) {
        $("formErr").textContent = `Please enter a valid number for ${k}.`;
        return false;
      }
    }
  }
  return true;
}

function populateReview() {
  const type = $("fType")?.value || "Centrifugal Pump";
  const id = ($("fId")?.value || "").trim() || "—";
  const issue = ($("fIssue")?.value || "").trim() || "—";
  const events = ($("fEvents")?.value || "").trim() || "—";
  const m = MACHINE_META[type] || MACHINE_META["Centrifugal Pump"];

  if ($("reviewImg")) $("reviewImg").src = m.img;
  if ($("reviewTag")) $("reviewTag").textContent = m.tag;
  if ($("reviewMachineName")) $("reviewMachineName").textContent = type;
  if ($("reviewId")) $("reviewId").textContent = id;
  if ($("reviewIssue")) $("reviewIssue").textContent = issue;
  if ($("reviewEvents")) $("reviewEvents").textContent = events;

  const sensors = sensorsFromForm();
  const entries = Object.entries(sensors);
  if ($("reviewSensors")) {
    if (!entries.length) {
      $("reviewSensors").innerHTML = `<span class="muted sm">No sensor readings entered — triage will proceed using symptoms and manuals.</span>`;
    } else {
      $("reviewSensors").innerHTML = entries.map(([k, v]) => {
        const schema = (META && META.sensor_schema && META.sensor_schema[k]) || {label: k, unit: ""};
        return `<span class="sensor-pill"><b>${esc(schema.label)}:</b> ${esc(v)} ${esc(schema.unit)}</span>`;
      }).join("");
    }
  }
}

function setWizardStep(n) {
  if (n > currentWizardStep) {
    for (let s = currentWizardStep; s < n; s++) {
      if (!validateStep(s)) return;
    }
  }
  $("formErr").textContent = "";
  currentWizardStep = n;

  document.querySelectorAll(".wizard-step").forEach((el, idx) => {
    el.classList.toggle("active", idx + 1 === n);
  });

  document.querySelectorAll("#reportStepper .step-item").forEach((item) => {
    const s = Number(item.dataset.step);
    item.classList.toggle("active", s === n);
    item.classList.toggle("done", s < n);
  });

  if ($("s2BadgeText")) {
    const type = $("fType")?.value || "Centrifugal Pump";
    const id = ($("fId")?.value || "").trim() || (MACHINE_META[type]?.placeholderId || "PUMP-101");
    $("s2BadgeText").textContent = `${type} · ${id}`;
  }

  if (n === 4) populateReview();

  const card = document.querySelector(".wizard-card");
  if (card) card.scrollIntoView({behavior: "smooth", block: "nearest"});
}

/* ---------- boot ---------- */
async function boot() {
  try {
    META = (await api("/api/meta")).data;
    $("fType").innerHTML = META.equipment_types.map((t) => `<option>${esc(t)}</option>`).join("");
    $("kbType").innerHTML = `<option value="">all machines</option>` + META.equipment_types.map((t) => `<option>${esc(t)}</option>`).join("");
    $("ruleTable").innerHTML = `<tr><th>Rule</th><th>How bad</th><th>When it fires</th></tr>` +
      META.thresholds.map((r) => `<tr><td><b>${esc(r.id)}</b></td><td>${esc(r.severity)}</td><td>${esc(r.description)}</td></tr>`).join("");
    const UNIT_CHOICE = {temperature_c: [["C", "°C"], ["F", "°F"]], pressure_bar: [["bar", "bar"], ["psi", "psi"]]};
    $("sensorGrid").innerHTML = Object.entries(META.sensor_schema).map(([k, m]) => {
      const opts = UNIT_CHOICE[k];
      const unitCtl = opts
        ? `<div class="unit-row"><input data-sensor="${k}" type="number" step="any" placeholder="leave empty if unknown" /><select data-unit="${k}" aria-label="Unit for ${esc(m.label)}">${opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>`
        : `<input data-sensor="${k}" type="number" step="any" placeholder="leave empty if unknown" />`;
      return `<label>${esc(m.label)}${opts ? "" : ` (${esc(m.unit)})`}${unitCtl}</label>`;
    }).join("");
    $("kbCount2").textContent = "19 manual pages · 6 machine types";
    $("typeHint").textContent = "We know these machines: " + META.equipment_types.join(" · ");

    if ($("machineTiles")) {
      $("machineTiles").innerHTML = META.equipment_types.map((t) => {
        const m = MACHINE_META[t] || {ico: "⚙"};
        return `<button type="button" class="machine-tile" data-mtype="${esc(t)}"><span class="tile-ico">${m.ico}</span><span>${esc(t)}</span></button>`;
      }).join("");
      document.querySelectorAll("#machineTiles .machine-tile").forEach((b) => {
        b.addEventListener("click", () => updateMachinePreview(b.dataset.mtype));
      });
    }

    $("fType")?.addEventListener("change", (e) => { updateMachinePreview(e.target.value); loadKnownIds(e.target.value); });
    updateMachinePreview($("fType")?.value || "Centrifugal Pump");
    loadKnownIds($("fType")?.value || "Centrifugal Pump");
    $("fIdSel")?.addEventListener("change", (e) => {
      if (e.target.value && e.target.value !== "__new__") $("fId").value = e.target.value;
    });
    $("fId")?.addEventListener("input", () => {
      const sel = $("fIdSel");
      if (sel && ![...sel.options].some((o) => o.value === $("fId").value.trim().toUpperCase())) sel.value = "__new__";
    });
  } catch { toast("Could not reach the server — is it running? (double-click start.bat)", "err"); return; }
  await refreshAI(); loadDashboard();
}

/* AI mode is backend-driven only (server key via provider env vars).
   The frontend never handles keys — it only reads the mode for display. */
async function refreshAI() {
  try { SERVER_AI = (await api("/api/ai/status")).data; }
  catch { SERVER_AI = null; }
}

/* Known-machine picker: same ID text every time, so one machine keeps one history. */
async function loadKnownIds(type) {
  const sel = $("fIdSel");
  if (!sel) return;
  try {
    const {data: j} = await api("/api/equipment");
    const ids = (j.equipment || []).filter((e) => e.equipment_type === type).map((e) => e.equipment_id);
    sel.innerHTML = `<option value="__new__">＋ New machine number…</option>` +
      ids.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
  } catch { sel.innerHTML = `<option value="__new__">＋ New machine number…</option>`; }
}

/* ---------- stepper & wizard controls ---------- */
document.querySelectorAll("#reportStepper .step-item").forEach((item) => {
  item.addEventListener("click", () => {
    const target = Number(item.dataset.step);
    if (target < currentWizardStep || validateStep(currentWizardStep)) {
      setWizardStep(target);
    }
  });
});

$("nextToStep2")?.addEventListener("click", () => setWizardStep(2));
$("backToStep1")?.addEventListener("click", () => setWizardStep(1));
$("nextToStep3")?.addEventListener("click", () => setWizardStep(3));
$("backToStep2")?.addEventListener("click", () => setWizardStep(2));
$("skipSensorsBtn")?.addEventListener("click", () => setWizardStep(4));
$("nextToStep4")?.addEventListener("click", () => setWizardStep(4));
$("backToStep3")?.addEventListener("click", () => setWizardStep(3));

$("editStep1Btn")?.addEventListener("click", () => setWizardStep(1));
$("editStep2Btn")?.addEventListener("click", () => setWizardStep(2));
$("editStep3Btn")?.addEventListener("click", () => setWizardStep(3));

/* Quick chips */
document.querySelectorAll("[data-append-issue]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const val = btn.dataset.appendIssue;
    const cur = ($("fIssue").value || "").trim();
    $("fIssue").value = cur ? `${cur}, ${val}` : val;
    $("fIssue").focus();
  });
});
document.querySelectorAll("[data-append-events]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const val = btn.dataset.appendEvents;
    const cur = ($("fEvents").value || "").trim();
    if (val.toLowerCase() === "none observed") {
      $("fEvents").value = "none observed";
    } else {
      $("fEvents").value = cur ? `${cur} ${val}` : val;
    }
    $("fEvents").focus();
  });
});

/* ---------- home ---------- */
const COVERAGE = [
  ["Report a problem", "Report page → analysed automatically"],
  ["Manuals & fixes", "Manuals page → 19 pages, 6 machine types"],
  ["Guesses, never diagnosis", "Results always say GUESS + show confidence"],
  ["Questions for you", "Missing info is asked, answers re-analyse"],
  ["On-site checks", "Every check names its manual source"],
  ["Urgency P1–P4", "Fixed meter rules decide the floor"],
  ["Job cards", "Always drafts until you approve"],
  ["History", "Reports page → per-machine timeline"],
  ["Meter rules", "11 fixed rules, shown on the report page"],
  ["Approve / reject", "Work orders page, your name required"],
];
async function loadDashboard() {
  const {data: j} = await api("/api/dashboard");
  if (!j.ok) { $("statCards").innerHTML = `<p class="error">Could not load. Is the server running?</p>`; return; }
  const s = j.stats;
  $("statCards").innerHTML = `
    <div class="stat"><svg class="stat-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg><span class="n">${s.reports}</span><span class="l">Problems reported</span></div>
    <div class="stat"><svg class="stat-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><span class="n">${s.draft}</span><span class="l">Waiting for decision</span></div>
    <div class="stat"><svg class="stat-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="20 6 9 17 4 12"/></svg><span class="n">${s.approved}</span><span class="l">Jobs approved</span></div>
    <div class="stat"><svg class="stat-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 12H6.5"/><path d="M6.5 17H18"/></svg><span class="n">${s.kb_sections}</span><span class="l">Manual pages</span></div>`;
  $("woCount").hidden = !s.draft; $("woCount").textContent = s.draft;
  const rc = $("repCount");
  if (rc) { rc.hidden = !s.reports; rc.textContent = s.reports; }
  $("homeTour").hidden = s.reports > 0;
  $("homeDraftCount").textContent = s.draft ? `${s.draft} job card${s.draft > 1 ? "s" : ""}` : "No job cards";
  $("dashRecent").innerHTML = (j.recent_reports || []).map((r) =>
    `<div class="tl-item"><b>${esc(r.equipment_id)}</b> <span class="muted">${esc(r.equipment_type)} · ${timeAgo(r.created_at)}</span><br/>${esc((r.issue_description || "").slice(0, 100))}…<br/><button class="btn ghost sm" data-r="${r.id}">See result →</button></div>`).join("") || `<p class="muted">Nothing yet — report the first problem above.</p>`;
  $("dashDrafts").innerHTML = (j.pending_drafts || []).map((w) =>
    `<div class="woitem"><b>${esc(w.title)}</b><br/><span class="pri">${esc(w.priority)}</span> <button class="btn ghost sm" data-wo="${w.id}">Decide →</button></div>`).join("") || `<p class="muted">All clear — nothing waiting. 🎉</p>`;
  $("coverage").innerHTML = COVERAGE.map(([a, b]) => `<div>✓ <b>${esc(a)}</b><br/><span class="muted">${esc(b)}</span></div>`).join("");
  document.querySelectorAll("#dashRecent [data-r]").forEach((b) => b.addEventListener("click", () => openReport(b.dataset.r)));
  document.querySelectorAll("#dashDrafts [data-wo]").forEach((b) => b.addEventListener("click", () => { goto("workorders"); openWO(b.dataset.wo); }));
  loadLogs();
}
async function loadLogs() {
  const {data: j} = await api("/api/logs?limit=8");
  const nice = {"report_created": "📝 report", "triage_run": "🔍 analysis", "wo_decision": "✔ decision", "confirmed": "✅ confirmed", "wo_edited": "✏️ edited", "http": "🌐", "app_start": "▶ started", "ai_test": "🤖 key test"};
  $("dashLogs").innerHTML = ((j.events || []).filter((e) => e.event !== "http").map((e) =>
    `<div class="tl-item">${esc(nice[e.event] || e.event)} · <span class="muted">${esc(e.ts || "").slice(0, 16).replace("T", " ")}</span> ${esc(e.msg || "")}</div>`).join("")) || `<p class="muted">No activity yet.</p>`;
}
$("logReload").addEventListener("click", loadLogs);

/* ---------- report form ---------- */
function sensorUnit(k) {
  const s = document.querySelector(`[data-unit="${k}"]`);
  return s ? s.value : null;
}
function sensorsFromForm() {
  const o = {};
  document.querySelectorAll("[data-sensor]").forEach((i) => {
    if (i.value === "") return;
    let v = Number(i.value);
    const u = sensorUnit(i.dataset.sensor); // convert to canonical units at entry
    if (i.dataset.sensor === "temperature_c" && u === "F") v = (v - 32) * 5 / 9;
    if (i.dataset.sensor === "pressure_bar" && u === "psi") v = v * 0.0689476;
    o[i.dataset.sensor] = Math.round(v * 100) / 100;
  });
  return o;
}
$("exampleBtn").addEventListener("click", () => {
  $("fType").value = "Centrifugal Pump";
  updateMachinePreview("Centrifugal Pump");
  $("fId").value = "PUMP-101";
  if ($("fIdSel")) $("fIdSel").value = "__new__";
  $("fIssue").value = "Grinding noise near the bearings, machine feels hot, vibration getting worse over 2 days";
  $("fEvents").value = "Stopped once yesterday, restarted fine. Oil changed 5 months ago.";
  document.querySelectorAll("[data-sensor]").forEach((i) => i.value = "");
  const tempInp = document.querySelector('[data-sensor="temperature_c"]');
  if (tempInp) tempInp.value = 96;
  const vibInp = document.querySelector('[data-sensor="vibration_mm_s"]');
  if (vibInp) vibInp.value = 8.2;
  setWizardStep(4);
  toast("✨ Example loaded — review below or step back through 1-3 to inspect.", "ok");
});

$("reportForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("formErr").textContent = "";
  if (!validateStep(1) || !validateStep(2) || !validateStep(3)) return;

  const body = {
    equipment_type: $("fType").value,
    equipment_id: $("fId").value.trim(),
    issue_description: $("fIssue").value.trim(),
    operating_events: $("fEvents").value.trim(),
    sensor_readings: sensorsFromForm()
  };

  $("submitBtn").disabled = true;
  $("submitBtn").textContent = "⏳ Analysing… please wait";
  try {
    const {data: j} = await api("/api/reports", {method: "POST", body: JSON.stringify(body)});
    if (!j.ok) throw new Error(j.error || "request failed");
    currentReport = j.report_id;
    renderTriage(j.triage, j.report_id, j.work_order_id);
    goto("triage");
    toast(j.triage?.ai?.draft_status === "ok" ? "Done — smart analysis ready. ✨" : "Done — analysis ready.", "ok");
  } catch (ex) {
    $("formErr").textContent = "Could not analyse: " + ex.message;
  } finally {
    $("submitBtn").disabled = false;
    $("submitBtn").textContent = "▶ Analyse my problem";
  }
});

/* ---------- triage result ---------- */
function sevTag(sev) { return sev === "critical" ? `<span class="tag danger">URGENT</span>` : `<span class="tag warn">check</span>`; }
function woSectionsHTML(wo) {
  const s = wo.sections;
  if (!s || !s.observed)  // older drafts: legacy paragraph fallback
    return `<p>${esc(wo.description).replace(/\n/g, "<br/>")}</p>`;
  return `<div class="wo-rows">
    <div class="wo-row"><span>Observed</span><div>${esc(s.observed)}</div></div>
    <div class="wo-row"><span>Events</span><div>${esc(s.events || "—")}</div></div>
    <div class="wo-row"><span>Possible causes</span><div>${(s.possible_causes || []).map((c) => `<span class="cause-chip">${esc(c.replace(" (UNCONFIRMED)", ""))}</span>`).join("") || "—"}</div></div>
    ${s.ai_note ? `<div class="wo-row"><span>AI note</span><div>${esc(s.ai_note)} <span class="muted">(unconfirmed)</span></div></div>` : ""}
  </div>`;
}
function plainWords(t, woId) {
  const lvl = t.priority?.level || "P4 - Routine";
  const top = (t.possible_causes || [])[0];
  const nCrit = (t.threshold_checks || []).filter((c) => c.severity === "critical").length;
  let mood = "Routine — keep an eye on it.";
  if (lvl.startsWith("P1")) mood = "Act now — this looks dangerous for the machine.";
  else if (lvl.startsWith("P2")) mood = "Look at it within a day — don't leave it running alone.";
  else if (lvl.startsWith("P3")) mood = "Plan a fix within a week.";
  return `<b>In plain words:</b> ${esc(mood)} ${nCrit ? `<b>${nCrit} meter reading${nCrit > 1 ? "s are" : " is"} in the danger zone.</b> ` : ""}${top ? `Best guess (not confirmed): ${esc(top.title.replace(" (UNCONFIRMED)", ""))}. ` : ""}A job card is drafted below — <b>you</b> decide what happens next.`;
}
let currentTriageTab = 1;
let triageViewMode = "phased";

function setTriageViewMode(mode) {
  triageViewMode = mode;
  $("btnPhasedMode")?.classList.toggle("active", mode === "phased");
  $("btnFullReportMode")?.classList.toggle("active", mode === "full");
  const out = $("triageOut");
  if (out) {
    out.classList.toggle("full-report-mode", mode === "full");
  }
  if (mode === "phased") {
    setTriageTab(currentTriageTab || 1);
  }
}

$("btnPhasedMode")?.addEventListener("click", () => setTriageViewMode("phased"));
$("btnFullReportMode")?.addEventListener("click", () => setTriageViewMode("full"));
$("printReportBtn")?.addEventListener("click", () => {
  setTriageViewMode("full");
  setTimeout(() => window.print(), 250);
});

function setTriageTab(n) {
  currentTriageTab = n;
  document.querySelectorAll(".ttab-pane").forEach((el, idx) => {
    el.classList.toggle("active", idx + 1 === n);
  });
  document.querySelectorAll("#tflowNav .tflow-btn").forEach((btn) => {
    const s = Number(btn.dataset.ttab);
    btn.classList.toggle("active", s === n);
  });
  const exec = document.querySelector(".triage-exec-card");
  if (exec && n > 1 && triageViewMode === "phased") {
    exec.scrollIntoView({behavior: "smooth", block: "start"});
  }
}

document.querySelectorAll("#tflowNav .tflow-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    setTriageViewMode("phased");
    setTriageTab(Number(btn.dataset.ttab));
  });
});
$("tflowNext1")?.addEventListener("click", () => setTriageTab(2));
$("tflowPrev2")?.addEventListener("click", () => setTriageTab(1));
$("tflowNext2")?.addEventListener("click", () => setTriageTab(3));
$("tflowPrev3")?.addEventListener("click", () => setTriageTab(2));
$("tflowNext3")?.addEventListener("click", () => setTriageTab(4));
$("tflowPrev4")?.addEventListener("click", () => setTriageTab(3));
$("quickJumpToWO")?.addEventListener("click", () => {
  setTriageViewMode("phased");
  setTriageTab(4);
});

function renderTriage(t, reportId, woId) {
  $("triageEmpty").hidden = true; $("triageOut").hidden = false;
  const lvl = t.priority?.level || "P4 - Routine";
  const pcls = lvl.startsWith("P1") ? "P1" : lvl.startsWith("P2") ? "P2" : lvl.startsWith("P3") ? "P3" : "P4";
  const heroIco = lvl.startsWith("P1") ? "🚨" : lvl.startsWith("P2") ? "⚠️" : lvl.startsWith("P3") ? "🗓️" : "✅";
  const nCrit = (t.threshold_checks || []).filter((c) => c.severity === "critical").length;
  const nWarn = (t.threshold_checks || []).filter((c) => c.severity === "warn").length;
  const top = (t.possible_causes || [])[0];

  // Official Engineering Document Header
  $("triageHero").className = "hero " + pcls;
  $("triageHero").innerHTML = `
    <div class="hero-ico">${heroIco}</div>
    <div style="flex:1;min-width:0">
      <div class="hero-meta-top">
        <span class="hero-doc-tag">EQUIPMENT TRIAGE &amp; DIAGNOSTIC REPORT</span>
        <span class="tag ${pcls === "P1" ? "danger" : pcls === "P2" ? "warn" : "ok"}">${esc(lvl)}</span>
      </div>
      <h2>${esc(t.equipment_type || "Equipment")} · ${esc(t.equipment_id || "—")}</h2>
      <p>${esc(t.priority?.rationale || "Automated diagnostic rule triage complete.")}</p>
      <div class="hero-id">
        <span>Report <b>${esc(reportId || "—")}</b></span>
        ${woId ? `<span>· Job Card <b>${esc(woId)}</b> (Draft)</span>` : ""}
      </div>
    </div>`;

  // Executive Synthesis & Status Chips
  let mood = "Routine — monitor for trend in standard preventive window.";
  if (lvl.startsWith("P1")) mood = "Act now — critical condition detected, immediate intervention required.";
  else if (lvl.startsWith("P2")) mood = "Urgent — investigate within 24 hours to avoid unscheduled stoppage.";
  else if (lvl.startsWith("P3")) mood = "Planned — schedule inspection and servicing within next weekly cycle.";

  $("plainWords").innerHTML = `
    <b>Executive Assessment:</b> ${esc(mood)} ${nCrit ? `<b>${nCrit} meter reading${nCrit > 1 ? "s are" : " is"} in the critical danger zone.</b> ` : ""}${top ? `Primary hypothesis: <b>${esc(top.title.replace(" (UNCONFIRMED)", ""))}.</b> ` : ""}A structured maintenance job card has been drafted for authorized technician sign-off.
    <div class="verdict-chips">
      <span class="vchip ${nCrit ? "crit" : nWarn ? "warn" : "ok"}">${nCrit ? `🚨 ${nCrit} Danger Breach` : nWarn ? `⚠️ ${nWarn} Warning` : "✅ 11 Threshold Rules Normal"}</span>
      <span class="vchip info">${t.ai?.draft_status === "ok" ? "✨ AI Assistant Active" : "⚙ Deterministic Rules Engine"}</span>
      <span class="vchip">📖 ${(t.retrieved_sections || []).length} Manual Citations</span>
      ${woId ? `<span class="vchip">📋 Work Order ${esc(woId)}</span>` : ""}
    </div>`;

  // KPI Strips
  $("tKpis").innerHTML = `
    <div class="kpi"><span class="k">DANGER</span><b class="v">${nCrit ? `🚨 ${nCrit}` : "0"}</b></div>
    <div class="kpi"><span class="k">WARNINGS</span><b class="v">${nWarn}</b></div>
    <div class="kpi"><span class="k">MANUALS</span><b class="v">📖 ${(t.retrieved_sections || []).length}</b></div>
    <div class="kpi"><span class="k">CAUSES</span><b class="v">🔍 ${(t.possible_causes || []).length}</b></div>`;

  // Actionable Critical Alerts Only (drop redundant "all normal" alert box)
  const al = [];
  (t.threshold_checks || []).filter((c) => c.severity === "critical").forEach((c) =>
    al.push(`<div class="alert crit"><b>🚨 Danger reading:</b> ${esc(c.message)} <span class="cite">${esc(c.citation)}</span></div>`));
  (t.threshold_checks || []).filter((c) => c.severity === "warn").forEach((c) =>
    al.push(`<div class="alert warn"><b>⚠ Worth checking:</b> ${esc(c.message)}</div>`));
  (t.data_quality?.conflicts || []).forEach((c) => al.push(`<div class="alert warn"><b>⚠ Numbers disagree with words:</b> ${esc(c.message)}</div>`));
  (t.data_quality?.invalid || []).forEach((c) => al.push(`<div class="alert warn"><b>⚠ Strange reading:</b> ${esc(c.message)}</div>`));
  if (t.retrieval_status?.status !== "ok")
    al.push(`<div class="alert fail"><b>Manual search failed:</b> ${esc(t.retrieval_status?.message)} Tip: use words like vibration, leak, overheating, noise, pressure.</div>`);
  $("alerts").innerHTML = al.join("");

  // Phase 1: Observations & Telemetry checks
  $("tObs").innerHTML = (t.observations || []).map((o) => `<li>${esc(o)}</li>`).join("");
  if ((t.threshold_checks || []).length) {
    $("tChecks").innerHTML = `<div class="telemetry-list">${(t.threshold_checks || []).map((c) =>
      `<div class="telemetry-row ${c.severity}"><span class="tag ${c.severity === "critical" ? "danger" : "warn"}">${c.severity.toUpperCase()}</span> <b>${esc(c.rule_id)}</b> · ${esc(c.sensor)} = <b>${esc(c.value)}</b> — <span class="muted">${esc(c.message)}</span></div>`).join("")}</div>`;
  } else {
    $("tChecks").innerHTML = `<div class="telemetry-ok"><span class="tag ok">ALL PASSED</span> All 11 deterministic rule thresholds verified — sensor telemetry is within safe operating parameters.</div>`;
  }

  const dq = t.data_quality || {};
  const miss = (dq.missing || []).map((m) => META.sensor_schema[m] ? `${META.sensor_schema[m].label}` : m).join(", ");
  $("tDQ").innerHTML = (dq.missing?.length ? `<p><b>Missing readings:</b> ${esc(miss)} <span class="muted">(unreported parameters will not block triage)</span></p>` : "") +
    ((dq.conflicts || []).map((c) => `<p>⚠ ${esc(c.message)}</p>`).join("") || "");

  // Phase 2: Probable Causes
  $("tCauses").innerHTML = (t.possible_causes || []).map((c, idx) => {
    const pct = Math.round((c.confidence || 0) * 100);
    const isAI = c.source === "gemini" || c.source === "openrouter";
    return `<div class="cause ${isAI ? "ai" : "local"}">
      <div class="cause-top">
        <span class="tag danger">HYPOTHESIS #${idx + 1} · UNCONFIRMED</span>
        ${isAI ? `<span class="tag src">✨ ${esc(String(c.source).toUpperCase())}</span>` : `<span class="tag src">⚙ LOCAL RULE</span>`}
        <span class="pct">${pct}% confidence</span>
      </div>
      <b>${esc(c.title)}</b>
      <div class="conf" title="confidence ${pct}%"><i style="width:${pct}%"></i></div>
      <p style="margin:4px 0 6px">${esc(c.note)}</p>
      <span class="cite">Source Citations: ${esc((c.citations || []).join(", "))}</span>
    </div>`;
  }).join("");

  // Phase 3: Inspection Checklist & Clarification Card
  $("retrBadge").innerHTML = t.retrieval_status?.status === "ok"
    ? `<span class="tag ok">${(t.retrieved_sections || []).length} pages found</span>` : `<span class="tag danger">search failed</span>`;
  $("tRetr").innerHTML = (t.retrieved_sections || []).map((r) =>
    `<div class="kbsec">
      <div class="kbsec-head">
        <b>[${esc(r.id)}] ${esc(r.title)}</b>
        <span class="cite">${esc(r.equipment_type || "")}</span>
      </div>
      <div class="kbsec-body">
        <p style="margin:4px 0 6px">${esc(r.content)}</p>
        <span class="cite">Action Procedure: <b>${esc(r.inspection_ref || "")}</b></span>
      </div>
    </div>`).join("") || `<p class="error">No manual pages found.</p>`;

  $("tSteps").innerHTML = (t.inspection_steps || []).map((s, i) =>
    `<li class="chk"><input type="checkbox" data-tick="${i}" /><div><b>${esc(s.step)}</b>${s.detail ? `<p class="muted">${esc(s.detail)}</p>` : ""}<span class="cite chip-cite">[${esc(s.citation)}]</span>${s.source === "gemini" ? ` <span class="tag src">✨</span>` : ""}</div></li>`).join("");

  function updateChkProgress() {
    const boxes = document.querySelectorAll("#tSteps [data-tick]");
    const total = boxes.length;
    const checked = [...boxes].filter((b) => b.checked).length;
    if ($("chkProgressBadge")) {
      $("chkProgressBadge").textContent = total ? `${checked} / ${total} Done (${Math.round((checked / total) * 100)}%)` : "Interactive";
    }
  }

  // Persist ticks per report
  try {
    const key = "ticks:" + (reportId || "noreport");
    const saved = JSON.parse(localStorage.getItem(key) || "[]");
    document.querySelectorAll("#tSteps [data-tick]").forEach((box) => {
      box.checked = saved.includes(+box.dataset.tick);
      box.addEventListener("change", () => {
        const cur = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
        if (box.checked) cur.add(+box.dataset.tick); else cur.delete(+box.dataset.tick);
        localStorage.setItem(key, JSON.stringify([...cur]));
        updateChkProgress();
      });
    });
    updateChkProgress();
    wireRowToggle($("tSteps"));
  } catch { /* private-mode fallback */ }

  // Compact Clarification Questions Card
  const qs = t.followup_questions || [];
  const clarCard = $("clarificationCard");
  if (clarCard) {
    if (!qs.length) {
      clarCard.hidden = true;
    } else {
      clarCard.hidden = false;
      if ($("qCountBadge")) $("qCountBadge").textContent = `${qs.length} questions`;
      $("tQ").innerHTML = qs.map((q, i) => `
        <div class="qa-item" data-qidx="${i}">
          <div class="qa-head">
            <span class="qa-badge">Q${i + 1}</span>
            <span class="qa-text">${esc(q)}</span>
          </div>
          <div class="qa-input-wrap">
            <input type="text" data-qa="${i}" data-q="${esc(q)}" placeholder="Type your answer or measurement here..." autocomplete="off" />
          </div>
        </div>
      `).join("");
      // Structured readings: missing meters get number fields that flow back
      // into sensor_readings on submit — no prose parsing needed.
      const miss = (t.data_quality?.missing || []).filter((k) => META?.sensor_schema?.[k]);
      $("fupSensors").innerHTML = miss.length
        ? `<h4 style="margin:12px 0 4px">Quick readings <small class="muted">fill what you measured, skip the rest</small></h4>` +
          miss.map((k) => { const m = META.sensor_schema[k];
            return `<label class="fup-sensor">${esc(m.label)} (${esc(m.unit)})<input type="number" step="any" data-fsensor="${k}" placeholder="—" /></label>`;
          }).join("")
        : "";
    }
  }

  // Phase 4: Job Card Draft & Urgency Rationale
  $("tPri").innerHTML = `<p style="font-size:18px"><b>${esc(lvl)}</b></p><p>${esc(t.priority?.rationale || "")}</p>`;
  const wo = t.work_order_draft || {};
  $("tWO").innerHTML = `
    <div class="woitem">
      <b>${esc(wo.title)}</b><br/>
      <span class="pri">${esc(wo.priority)}</span> <span class="st draft">DRAFT — AWAITING TECHNICIAN SIGN-OFF</span>
      ${woSectionsHTML(wo)}
      <span class="cite">Sources: ${esc((wo.citations || []).join(", "))}</span><br/>
      <button class="btn primary" id="gotoWO" style="margin-top:10px">Review &amp; Decide on Job Card →</button>
    </div>`;

  const g = $("gotoWO");
  if (g) g.addEventListener("click", () => { goto("workorders"); if (woId) openWO(woId); });

  setTriageViewMode("phased");
  setTriageTab(1);
}

$("fupBtn")?.addEventListener("click", async () => {
  if (!currentReport) return toast("Send a report first.", "err");
  const boxes = [...document.querySelectorAll("[data-qa]")].map((t, i) => {
    const qText = t.dataset.q || (t.closest(".qa-item")?.querySelector(".qa-text")?.textContent.trim()) || ("Q" + (i + 1));
    return {q: i, a: t.value.trim(), question: qText};
  }).filter((x) => x.a);
  const extraSensors = {};
  document.querySelectorAll("[data-fsensor]").forEach((i) => {
    if (i.value !== "" && !Number.isNaN(Number(i.value))) extraSensors[i.dataset.fsensor] = Number(i.value);
  });

  if (!boxes.length && !Object.keys(extraSensors).length) return toast("Answer at least one question (or enter a reading) first.", "err");
  const answers = boxes.map((b) => ({question: b.question, answer: b.a}));
  const {data: j} = await api(`/api/reports/${currentReport}/followup`, {method: "POST", body: JSON.stringify({answers, sensor_readings: extraSensors})});
  if (j.ok) { renderTriage(j.triage, currentReport, null); toast("Updated analysis ready — old one kept in history.", "ok"); }
  else toast("Could not update: " + (j.error || ""), "err");
});

$("confBtn").addEventListener("click", async () => {
  if (!currentReport) return toast("Send a report first.", "err");
  const txt = $("confTxt").value.trim();
  if (!txt) return toast("Write what you verified first.", "err");
  const {data: j} = await api(`/api/reports/${currentReport}/confirm`, {method: "POST",
    body: JSON.stringify({confirmed_findings: [txt], confirmed_by: $("confBy").value.trim() || "technician"})});
  $("confMsg").textContent = j.ok ? "✓ Saved as CONFIRMED (by you). The guesses above stay guesses." : "Failed: " + (j.error || "");
  if (j.ok) toast("Saved as confirmed. ✓", "ok"); else toast("Failed: " + j.error, "err");
});

async function openReport(rid) {
  const {data: d} = await api(`/api/reports/${rid}`);
  if (!d.ok) return toast(d.error || "Not found", "err");
  currentReport = d.report.id;
  if (d.triage && Object.keys(d.triage).length) { renderTriage(d.triage, d.report.id, (d.work_orders || [])[0]?.id); goto("triage"); }
  else toast("This report has no analysis yet.", "err");
}

/* ---------- reports list ---------- */
async function loadReports() {
  $("repList").innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  const {data: j} = await api("/api/reports");
  if (!j.ok) { $("repList").innerHTML = `<p class="error">${esc(j.error || "")}</p>`; return; }
  allReportsCache = j.reports || [];
  paintReports();
}
function woPill(wos) {
  if (!wos?.length) return `<span class="muted">no job card</span>`;
  const s = wos[0].status;
  return `<span class="st ${esc(s)}">${s === "draft" ? "NEEDS DECISION" : esc(s).toUpperCase()}</span>`;
}
function machineImg(type) {
  const m = (typeof MACHINE_META !== "undefined" && MACHINE_META[type]) || {};
  return m.img || "img/centrifugal_pump.jpg";
}
function paintReports() {
  const q = ($("repSearch").value || "").toLowerCase();
  const urgRank = (p) => ({"P1 - Emergency": 0, "P2 - Urgent": 1, "P3 - Planned": 2, "P4 - Routine": 3}[p] ?? 9);
  let list = allReportsCache.filter((r) =>
    !q || `${r.equipment_id} ${r.equipment_type} ${r.issue_description}`.toLowerCase().includes(q));
  if (repFilter === "P1") list = list.filter((r) => (r.latest_priority || "").startsWith("P1"));
  else if (repFilter) list = list.filter((r) => repStatus(r) === repFilter);
  if (repUrg) list = list.filter((r) => r.latest_priority === repUrg);
  list = [...list].sort((a, b) => repSort === "urgent"
    ? (urgRank(a.latest_priority) - urgRank(b.latest_priority) || b.created_at - a.created_at)
    : b.created_at - a.created_at);
  // Group by day: Today / Yesterday / date.
  const groups = [];
  for (const r of list) {
    const d = new Date((r.created_at || 0) * 1000);
    const today = new Date(), yest = new Date(Date.now() - 864e5);
    const label = d.toDateString() === today.toDateString() ? "Today"
      : d.toDateString() === yest.toDateString() ? "Yesterday"
      : d.toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"});
    if (!groups.length || groups[groups.length - 1][0] !== label) groups.push([label, []]);
    groups[groups.length - 1][1].push(r);
  }
  const card = (r) =>
    `<div class="report-card"><div class="rc-main"><div class="rc-top"><b>${esc(r.equipment_id)}</b> <span class="muted">${esc(r.equipment_type)} · ${timeAgo(r.created_at)}</span></div>
     <div class="rc-pills"><span class="pri ${priCls(r.latest_priority)}">${esc(r.latest_priority || "—")}</span> <span class="st ${esc(repStatusCls(r))}">${esc(repStatus(r).toUpperCase())}</span></div>
     <p>${esc((r.issue_description || "").slice(0, 140))}${(r.issue_description || "").length > 140 ? "…" : ""}</p>
     <div class="row-inline"><button class="btn primary sm" data-r="${r.id}">See result →</button><button class="btn ghost sm" data-h="${esc(r.equipment_id)}">Machine history</button></div></div><img class="rc-img" src="${esc(machineImg(r.equipment_type))}" alt="${esc(r.equipment_type)}" loading="lazy" onerror="this.style.display='none'" /></div>`;
  $("repList").innerHTML = groups.map(([label, items]) =>
    `<h3 class="day-head">${esc(label)}</h3>` + items.map(card).join("")).join("")
    || `<div class="empty"><h2>${q || repFilter || repUrg ? "Nothing matches these filters" : "No reports yet"}</h2><p>${q || repFilter || repUrg ? "Try clearing a filter." : "Report the first problem — it takes 2 minutes."}</p>${q || repFilter || repUrg ? "" : `<button class="btn primary" data-goto="report">Report a problem</button>`}</div>`;
  document.querySelectorAll("#repList [data-r]").forEach((b) => b.addEventListener("click", () => openReport(b.dataset.r)));
  document.querySelectorAll("#repList [data-h]").forEach((b) => b.addEventListener("click", () => { $("hId").value = b.dataset.h; loadHistory(); }));
  document.querySelectorAll("#repList [data-goto]").forEach((b) => b.addEventListener("click", () => goto(b.dataset.goto)));
}
function repStatus(r) {
  const wos = r.work_orders || [];
  if (wos.some((w) => w.status === "draft")) return "Needs decision";
  if (wos.some((w) => w.status === "approved")) return "Approved";
  if (wos.some((w) => w.status === "rejected")) return "Rejected";
  if (wos.some((w) => w.status === "superseded")) return "Superseded";
  return "No job card";
}
function repStatusCls(r) {
  return {"Needs decision": "draft", Approved: "approved", Rejected: "rejected", Superseded: "superseded"}[repStatus(r)] || "draft";
}
document.querySelectorAll("#repChips .chip").forEach((c) => c.addEventListener("click", () => {
  document.querySelectorAll("#repChips .chip").forEach((x) => { x.classList.remove("active"); x.setAttribute("aria-pressed", "false"); });
  c.classList.add("active"); c.setAttribute("aria-pressed", "true");
  repFilter = c.dataset.rf; paintReports();
}));
document.querySelectorAll("#repChips .chip").forEach((c) => c.setAttribute("aria-pressed", c.classList.contains("active") ? "true" : "false"));
$("repUrg").addEventListener("change", (e) => { repUrg = e.target.value; paintReports(); });
$("repSort").addEventListener("change", (e) => { repSort = e.target.value; paintReports(); });
$("repSearch").addEventListener("input", paintReports);
$("hBtn").addEventListener("click", loadHistory);
async function loadHistory() {
  const raw = $("hId").value.trim(); if (!raw) return toast("Write a machine number first.", "err");
  const id = raw.toUpperCase().replace(/\s+/g, ""); // same normalisation as report IDs
  $("hOut").innerHTML = `<div class="skel"></div><div class="skel"></div>`; // instant feedback
  $("hOut").scrollIntoView({behavior: "smooth", block: "nearest"});
  const {data: j} = await api(`/api/history/${encodeURIComponent(id)}`);
  if (!j.ok) {
    $("hOut").innerHTML = `<p class="error">Could not load history: ${esc(j.error || "server error")}</p>`;
    return;
  }
  if (!j.reports.length && !j.work_orders.length) {
    $("hOut").innerHTML = `<div class="empty"><h2>No history available</h2><p>No reports or job cards for <b>${esc(id)}</b> yet.</p></div>`;
    return;
  }
  $("hOut").innerHTML = `<p><b>${j.reports.length}</b> report(s) · <b>${j.work_orders.length}</b> job card(s) · machine <b>${esc(id)}</b></p>` +
    j.reports.map((r, i) => `<div class="tl-item"><b>Report ${i + 1}</b> — ${esc((r.issue_description || "").slice(0, 120))}…<br/><small>Confirmed on site: ${esc((r.confirmed_findings || []).join(" | ") || "nothing yet")}</small> <button class="btn ghost sm" data-r="${r.id}">See result</button></div>`).join("") +
    j.work_orders.map((w) => `<div class="woitem"><span class="st ${esc(w.status)}">${esc(w.status).toUpperCase()}</span> <b>${esc(w.title)}</b></div>`).join("");
  document.querySelectorAll("#hOut [data-r]").forEach((b) => b.addEventListener("click", () => openReport(b.dataset.r)));
  $("hOut").scrollIntoView({behavior: "smooth", block: "nearest"});
}

/* ---------- CMMS Work Orders Register ---------- */
let allWOsCache = [];
document.querySelectorAll("#woChips .chip").forEach((c) => c.addEventListener("click", () => {
  document.querySelectorAll("#woChips .chip").forEach((x) => { x.classList.remove("active"); x.setAttribute("aria-pressed", "false"); });
  c.classList.add("active"); c.setAttribute("aria-pressed", "true"); woFilter = c.dataset.f; paintWOs();
}));
document.querySelectorAll("#woChips .chip").forEach((c) => c.setAttribute("aria-pressed", c.classList.contains("active") ? "true" : "false"));
$("woReload")?.addEventListener("click", loadWOs);
$("woPrint")?.addEventListener("click", () => {
  if ($("woDetail").hidden) return toast("Open a job card first, then print it.", "err");
  document.body.classList.add("print-wo");
  window.print();
});
window.addEventListener("afterprint", () => document.body.classList.remove("print-wo"));
$("woSearch")?.addEventListener("input", paintWOs);

async function loadWOs() {
  $("woList").innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  const {data: j} = await api("/api/work-orders");
  if (!j.ok) { $("woList").innerHTML = `<p class="error">${esc(j.error || "")}</p>`; return; }
  allWOsCache = j.work_orders || [];
  paintWOs();
}

function paintWOs() {
  const total = allWOsCache.length;
  const drafts = allWOsCache.filter((w) => w.status === "draft").length;
  const appr = allWOsCache.filter((w) => w.status === "approved").length;
  const rej = allWOsCache.filter((w) => w.status === "rejected").length;
  const sup = allWOsCache.filter((w) => w.status === "superseded").length;
  if ($("cntAll")) $("cntAll").textContent = total - sup; // "All" = live queue, superseded hidden
  if ($("cntSup")) $("cntSup").textContent = sup;
  if ($("cntDraft")) $("cntDraft").textContent = drafts;
  if ($("cntAppr")) $("cntAppr").textContent = appr;
  if ($("cntRej")) $("cntRej").textContent = rej;

  const q = ($("woSearch")?.value || "").toLowerCase().trim();
  let list = allWOsCache;
  if (woFilter) list = list.filter((w) => w.status === woFilter);
  else list = list.filter((w) => w.status !== "superseded"); // superseded live only under their chip
  if (q) list = list.filter((w) => `${w.id} ${w.title} ${w.priority} ${w.equipment_id || ""}`.toLowerCase().includes(q));

  if (!list.length) {
    $("woList").innerHTML = `<div class="empty"><h2>${q ? "No matching work orders" : "Nothing here"}</h2><p>${q ? "Try clearing your search query." : (woFilter === "draft" ? "All clear — no work orders awaiting decision. 🎉" : "No job cards in this state.")}</p></div>`;
    return;
  }

  $("woList").innerHTML = `
    <div class="wo-table-wrap">
      <table class="wo-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Priority</th>
            <th>WO Reference</th>
            <th>Problem Scope &amp; Title</th>
            <th style="text-align:right">Action</th>
          </tr>
        </thead>
        <tbody>
          ${list.map((w) => {
            const stCls = esc(w.status);
            const stLabel = w.status === "draft" ? "NEEDS DECISION" : esc(w.status).toUpperCase();
            return `
              <tr>
                <td><span class="st ${stCls}">${stLabel}</span></td>
                <td><span class="pri ${priCls(w.priority)}">${esc(w.priority || "—")}</span></td>
                <td class="wo-id-cell">
                  <b>${esc(w.id)}</b>
                  <small>${timeAgo(w.created_at)}</small>
                </td>
                <td class="wo-title-cell">
                  <b>${esc(w.title)}</b>
                </td>
                <td style="text-align:right">
                  ${w.status === "draft"
                    ? `<button class="btn primary sm" data-wo="${w.id}">Open &amp; decide →</button>`
                    : `<button class="btn ghost sm" data-wo="${w.id}">See details →</button>`}
                </td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
  document.querySelectorAll("[data-wo]").forEach((b) => b.addEventListener("click", () => openWO(b.dataset.wo)));
}
async function openWO(id) {
  const {data: j} = await api(`/api/work-orders/${id}`);
  if (!j.ok) return toast(j.error || "Not found", "err");
  const w = j.work_order; currentWO = w.id;
  $("woDetail").hidden = false;
  $("woTitle").textContent = w.title;
  $("woStatus").innerHTML = `<span class="st ${esc(w.status)}">${w.status === "draft" ? "NEEDS DECISION" : esc(w.status).toUpperCase()}</span> <span class="pri">${esc(w.priority)}</span>`;
  const decidedWO = w.status !== "draft";
  $("woBody").innerHTML = `<div class="wo-top"><div class="wo-top-main">${woSectionsHTML(w)}</div><img class="wo-img" src="${esc(machineImg(w.equipment_type))}" alt="${esc(w.equipment_type || "Equipment")}" loading="lazy" onerror="this.style.display='none'" /></div>
    <h3>Checks for this job</h3><p class="muted sm">${decidedWO ? "Final state — locked because this job is closed." : "Tick each box as you complete it — ticks are saved in this browser per job card."}</p><ol class="steps-list" id="woSteps">${(w.inspection_steps || []).map((s, i) => `<li class="chk"><input type="checkbox" data-wotick="${i}"${decidedWO ? " disabled" : ""} /><div>${esc(s)}</div></li>`).join("")}</ol>
    <p><small>Safety: ${esc(w.safety_notes)}<br/>Sources: <span class="cite">${esc((w.citations || []).join(", "))}</span></small></p>
    ${w.decision_note ? `<p><small>Decision note: ${esc(w.decision_note)}</small></p>` : ""}
    <h3>What happened so far</h3><ul class="hist">${(w.history || []).map((h) => `<li><b>${esc(h.by)}</b> — ${esc(h.action)}</li>`).join("")}</ul>`;
  // Clickable checklist on open drafts, persisted per job card in this browser
  // (tracking only — ticks never approve anything; decisions happen via
  // Approve/Reject). Decided jobs render disabled: history is frozen.
  try {
    const key = "woticks:" + w.id;
    const saved = JSON.parse(localStorage.getItem(key) || "[]");
    document.querySelectorAll("#woSteps [data-wotick]").forEach((box) => {
      box.checked = saved.includes(+box.dataset.wotick);
      if (box.disabled) return;
      box.addEventListener("change", () => {
        const cur = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
        if (box.checked) cur.add(+box.dataset.wotick); else cur.delete(+box.dataset.wotick);
        localStorage.setItem(key, JSON.stringify([...cur]));
      });
    });
    wireRowToggle($("woSteps"));
  } catch { /* private-mode browsers: ticks simply won't persist */ }
  $("woEditTitle").value = w.title; $("woEditDesc").value = w.description; $("woEditPri").value = w.priority;
  // Decided jobs are read-only: hide the edit/decision form so the same job
  // can never be submitted again; show who decided instead.
  const decided = decidedWO;
  $("woDecide").hidden = decided;
  const box = $("woDecided");
  if (decided) {
    const last = (w.history || []).filter((h) => /APPROVED|REJECTED|SUPERSEDED/.test(h.action)).slice(-1)[0];
    const when = last && last.at ? new Date(last.at * 1000).toLocaleString() : "";
    box.hidden = false;
    box.className = "decided " + w.status;
    const vLabel = w.status === "approved" ? "✓ Approved" : w.status === "rejected" ? "✕ Rejected" : "↺ Superseded by a newer draft";
    const vSub = w.status === "superseded" ? "Kept for history — the newer draft for this report is the active one." : "This job is closed — it cannot be edited or decided again.";
    box.innerHTML = `<b>${vLabel}${last ? ` by ${esc(last.by)}` : ""}</b>${when ? ` <span class="muted">· ${esc(when)}</span>` : ""}${w.decision_note ? `<br/>Note: ${esc(w.decision_note)}` : ""}<br/><span class="muted sm">${vSub}</span>`;
  } else {
    box.hidden = true;
    $("woMsg").textContent = "";
  }
  $("woDetail").scrollIntoView({behavior: "smooth", block: "nearest"});
}
$("woSave").addEventListener("click", async () => {
  if (!currentWO) return toast("Open a job card first.", "err");
  const steps = $("woAddStep").value.trim();
  const payload = {title: $("woEditTitle").value, description: $("woEditDesc").value,
    priority: $("woEditPri").value, edited_by: $("woActor").value.trim() || "technician"};
  if (steps) {
    const cur = (await api(`/api/work-orders/${currentWO}`)).data.work_order;
    payload.inspection_steps = [...(cur.inspection_steps || []), steps];
  }
  const {data: j} = await api(`/api/work-orders/${currentWO}`, {method: "PATCH", body: JSON.stringify(payload)});
  $("woMsg").textContent = j.ok ? "✓ Changes saved." : "Failed: " + (j.error || "");
  if (j.ok) { toast("Changes saved. ✓", "ok"); openWO(currentWO); loadWOs(); } else toast("Failed: " + j.error, "err");
});
$("woApprove").addEventListener("click", async () => {
  if (!currentWO) return;
  if (!$("woActor").value.trim()) return toast("Write your name first — mystery approvals are blocked. 🙂", "err");
  const {data: j} = await api(`/api/work-orders/${currentWO}/approve`, {method: "POST",
    body: JSON.stringify({approved_by: $("woActor").value.trim(), decision_note: $("woNote").value})});
  $("woMsg").textContent = j.ok ? "✓ Approved by you. The helper never approves on its own." : "Failed: " + (j.error || "");
  if (j.ok) { toast("Job approved. ✓", "ok"); openWO(currentWO); loadWOs(); } else toast("Failed: " + j.error, "err");
});
$("woReject").addEventListener("click", async () => {
  if (!currentWO) return;
  if (!$("woNote").value.trim()) return toast("Tell us why — a reason is needed to reject.", "err");
  const {data: j} = await api(`/api/work-orders/${currentWO}/reject`, {method: "POST",
    body: JSON.stringify({rejected_by: $("woActor").value.trim() || "technician", decision_note: $("woNote").value.trim()})});
  $("woMsg").textContent = j.ok ? "✓ Rejected, reason saved." : "Failed: " + (j.error || "");
  if (j.ok) { toast("Job rejected.", "ok"); openWO(currentWO); loadWOs(); } else toast("Failed: " + j.error, "err");
});

/* ---------- manuals ---------- */
async function searchKB() {
  const q = $("kbQ").value || "", t = $("kbType").value;
  const {data: j} = await api(`/api/kb?q=${encodeURIComponent(q)}&equipment_type=${encodeURIComponent(t)}`);
  $("kbOut").innerHTML = j.ok
    ? ((j.results || []).map((r) => `<div class="kbsec"><b>[${esc(r.id)}] ${esc(r.title)}</b> <span class="cite">${esc(r.equipment_type)}</span><br/>${esc(r.content)}<br/><span class="cite">Do this: ${esc(r.inspection_ref || "")}</span></div>`).join("") || `<div class="empty"><h2>No pages found</h2><p>Try: vibration, leak, overheating, noise, pressure.</p></div>`)
    : `<div class="alert fail"><b>Search failed:</b> ${esc(j.message || j.error || "")}</div>`;
}
$("kbBtn").addEventListener("click", searchKB);

boot();

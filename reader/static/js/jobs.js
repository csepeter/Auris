// Jobs page: durable background jobs with filters, incremental updates and
// screen-reader announcements. Pure helpers are exported for node tests.

/* ── Pure helpers ─────────────────────────────────────────────────────────── */

const JOB_STATE_LABELS = {
  pending: "Várakozik",
  queued: "Várakozik",
  running: "Folyamatban",
  complete: "Elkészült",
  completed: "Elkészült",
  failed: "Hiba",
  interrupted: "Megszakadt",
  cancelled: "Leállítva",
  cancelling: "Leállítás folyamatban",
};

const JOB_TYPE_LABELS = {
  generate_chapter: "Fejezethang generálása",
  generate_book: "Könyv hangjainak generálása",
  export_chapter: "Fejezet exportálása",
  export_book: "Könyv exportálása",
  export: "Exportálás",
  reanalyze: "Szereplők újraelemzése",
  initial_analysis: "Szereplőelemzés",
  qa_chapter: "Minőségellenőrzés",
  voice_suggestions: "Hangjavaslatok",
  speaker_review: "Beszélők ellenőrzése",
};

// Messages the server stores in English by default.
const JOB_MESSAGE_TRANSLATIONS = {
  "Starting...": "Indítás…",
  "Starting…": "Indítás…",
};

const ACTIVE_STATES = ["pending", "queued", "running", "cancelling"];
const DONE_STATES = ["complete", "completed"];
const PROBLEM_STATES = ["failed", "interrupted", "cancelled"];
const RESUMABLE_STATES = ["failed", "interrupted", "cancelled"];

function jobState(job) {
  const state = job?.state || job?.status || "pending";
  return state === "running" && job?.cancel_requested ? "cancelling" : state;
}
function jobStateLabel(state) {
  return JOB_STATE_LABELS[state] || state || "Ismeretlen";
}
function jobTypeLabel(type) {
  return JOB_TYPE_LABELS[type] || "Feladat";
}

// The message line, or "" when it only repeats the state or type label.
function jobMessage(job) {
  const raw = String(job?.message ?? "").trim();
  const text = JOB_MESSAGE_TRANSLATIONS[raw] || raw;
  if (!text) return "";
  const norm = (value) => String(value).trim().toLocaleLowerCase("hu").replace(/[.…!]+$/, "");
  const state = jobState(job);
  const same = [jobStateLabel(state), jobStateLabel(job?.state), jobTypeLabel(job?.type || job?.kind)];
  return same.some((label) => norm(label) === norm(text)) ? "" : text;
}

function jobFilterMatches(job, filter) {
  const state = jobState(job);
  if (filter === "active") return ACTIVE_STATES.includes(state);
  if (filter === "done") return DONE_STATES.includes(state);
  if (filter === "problem") return PROBLEM_STATES.includes(state);
  return true;
}

// SQLite stores "YYYY-MM-DD HH:MM:SS" in UTC; ISO strings pass through.
function parseServerTime(value) {
  if (!value) return null;
  let text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text)) text = text.replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(text)) text += "Z";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeTime(date, now = new Date()) {
  if (!date) return "";
  const sec = Math.round((now.getTime() - date.getTime()) / 1000);
  if (sec < 45) return "az imént";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} perce`;
  const hours = Math.round(min / 60);
  if (hours < 24) return `${hours} órája`;
  const days = Math.round(hours / 24);
  if (days === 1) return "tegnap";
  if (days < 30) return `${days} napja`;
  return date.toLocaleDateString("hu-HU");
}

function absoluteTime(date) {
  return date
    ? date.toLocaleString("hu-HU", { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "";
}

function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600),
    m = Math.floor((total % 3600) / 60),
    s = total % 60;
  if (h) return `${h} ó ${m} p`;
  if (m) return `${m} p ${s} mp`;
  return `${s} mp`;
}

function jobDuration(job, now = new Date()) {
  const start = parseServerTime(job?.started_at);
  if (!start) return "";
  const end = parseServerTime(job?.finished_at) || (ACTIVE_STATES.includes(jobState(job)) ? now : null);
  return end ? formatDuration(end.getTime() - start.getTime()) : "";
}

function jobPercent(job) {
  const total = Number(job?.total) || 0;
  return total ? Math.min(100, Math.max(0, Math.round(((Number(job.done) || 0) / total) * 100))) : 0;
}

const DOWNLOAD_KEYS = [
  ["download", "Export letöltése"],
  ["audio_download", "Hang letöltése"],
  ["subtitle_download", "Felirat letöltése"],
  ["zip_download", "Csomag letöltése"],
];

// Only same-origin /api/ URLs are offered as download links.
function jobDownloads(result, origin = "http://localhost") {
  if (!result || typeof result !== "object") return [];
  const links = [];
  for (const [key, label] of DOWNLOAD_KEYS) {
    const value = result[key];
    if (typeof value !== "string" || !value) continue;
    try {
      const url = new URL(value, origin);
      if (url.origin === origin && url.pathname.startsWith("/api/"))
        links.push({ key, label, href: url.pathname + url.search });
    } catch {
      /* ignore malformed links */
    }
  }
  return links;
}

function jobActions(job) {
  const state = jobState(job);
  return {
    cancel: ["pending", "queued", "running"].includes(state),
    resume: RESUMABLE_STATES.includes(state),
  };
}

if (typeof module === "object" && module.exports) {
  module.exports = {
    JOB_STATE_LABELS,
    JOB_TYPE_LABELS,
    jobState,
    jobStateLabel,
    jobTypeLabel,
    jobMessage,
    jobFilterMatches,
    parseServerTime,
    relativeTime,
    absoluteTime,
    formatDuration,
    jobDuration,
    jobPercent,
    jobDownloads,
    jobActions,
  };
}

/* ── DOM ──────────────────────────────────────────────────────────────────── */

const jobNodes = new Map(); // job id -> <li>
const jobSnapshots = new Map(); // job id -> last state (for announcements)
let currentFilter = "all",
  firstLoad = true,
  loadingJobs = false,
  lastJobs = [];
const FILTER_KEY = "auris.jobsFilter";

function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}
function setHidden(el, hidden) {
  if (el && el.hidden !== hidden) el.hidden = hidden;
}
function jobId(job) {
  return String(job.id || job.job_id);
}

function createJobNode(id) {
  const li = document.createElement("li");
  li.className = "job-item";
  li.dataset.jobId = id;
  const titleId = `job-title-${id.replace(/[^\w-]/g, "")}`;
  li.innerHTML =
    `<article class="job-card" aria-labelledby="${titleId}">` +
    `<div class="job-head"><span class="job-state" data-f="state"></span><span class="job-type" data-f="type"></span></div>` +
    `<h2 class="job-title" id="${titleId}"><a data-f="book"></a><span data-f="book-plain"></span><span class="job-chapter" data-f="chapter"></span></h2>` +
    `<p class="job-message" data-f="message"></p>` +
    `<div class="job-progress" data-f="progress"><progress max="100" data-f="bar"></progress><span class="job-progress-text" data-f="progress-text"></span></div>` +
    `<p class="job-error" data-f="error" role="note"></p>` +
    `<p class="job-warn" data-f="warn"></p>` +
    `<dl class="job-times">` +
    `<div data-f="created-row"><dt>Létrehozva</dt><dd><time data-f="created"></time></dd></div>` +
    `<div data-f="started-row"><dt>Indult</dt><dd><time data-f="started"></time></dd></div>` +
    `<div data-f="finished-row"><dt>Befejeződött</dt><dd><time data-f="finished"></time></dd></div>` +
    `<div data-f="duration-row"><dt>Időtartam</dt><dd data-f="duration"></dd></div>` +
    `</dl>` +
    `<div class="job-actions" data-f="actions"></div>` +
    `</article>`;
  return li;
}

function field(node, name) {
  return node.querySelector(`[data-f="${name}"]`);
}

function setTime(node, name, value, now) {
  const date = parseServerTime(value);
  setHidden(field(node, `${name}-row`), !date);
  const el = field(node, name);
  if (!date) return;
  const iso = date.toISOString();
  if (el.getAttribute("datetime") !== iso) {
    el.setAttribute("datetime", iso);
    el.title = absoluteTime(date);
  }
  setText(el, relativeTime(date, now));
}

function actionsHtml(job, id) {
  const esc = window.Auris.esc,
    icon = window.Auris.icon;
  const { cancel, resume } = jobActions(job);
  const title = job.book_title || job.result?.book_title || jobTypeLabel(job.type);
  let html = jobDownloads(job.result, location.origin)
    .map((l) => `<a class="btn btn-primary" href="${esc(l.href)}" download>${icon("download")}<span>${esc(l.label)}</span></a>`)
    .join("");
  if (cancel)
    html += `<button type="button" class="btn btn-ghost" data-job="${esc(id)}" data-action="cancel" aria-label="${esc(title)}: ${esc(jobTypeLabel(job.type))} leállítása">${icon("stop")}<span>Leállítás</span></button>`;
  if (resume)
    html += `<button type="button" class="btn btn-primary" data-job="${esc(id)}" data-action="resume" aria-label="${esc(title)}: ${esc(jobTypeLabel(job.type))} folytatása">${icon("play")}<span>Folytatás</span></button>`;
  return html;
}

function updateJobNode(node, job, now) {
  const id = jobId(job);
  const state = jobState(job);
  const typeLabel = jobTypeLabel(job.type || job.kind);
  node.className = `job-item state-${state}`;
  setText(field(node, "state"), jobStateLabel(state));
  setText(field(node, "type"), typeLabel);

  const title = job.book_title || job.result?.book_title || "";
  const bookLink = field(node, "book"),
    bookPlain = field(node, "book-plain");
  const bookId = Number(job.book_id);
  if (title && Number.isInteger(bookId) && bookId > 0) {
    const href = `/reader/${bookId}`;
    if (bookLink.getAttribute("href") !== href) bookLink.setAttribute("href", href);
    setText(bookLink, title);
    setHidden(bookLink, false);
    setText(bookPlain, "");
  } else {
    setHidden(bookLink, true);
    // No book title: a deleted book, or a job that is not tied to a book.
    setText(bookPlain, title || (bookId > 0 ? "Törölt könyv" : typeLabel));
  }
  setHidden(field(node, "type"), !title && !(bookId > 0));
  setText(field(node, "chapter"), job.chapter_title ? ` · ${job.chapter_title}` : "");

  const message = jobMessage(job);
  setText(field(node, "message"), message);
  setHidden(field(node, "message"), !message);

  const total = Number(job.total) || 0;
  setHidden(field(node, "progress"), !total);
  if (total) {
    const pct = jobPercent(job);
    const bar = field(node, "bar");
    if (bar.value !== pct) bar.value = pct;
    bar.setAttribute("aria-label", `${typeLabel} készültsége`);
    setText(field(node, "progress-text"), `${pct}% · ${Number(job.done) || 0} / ${total}`);
  }

  setText(field(node, "error"), job.error || "");
  setHidden(field(node, "error"), !job.error);
  const warn = job.result?.mastering_warning ? `A hangerő-kiegyenlítés kimaradt: ${job.result.mastering_warning}` : "";
  setText(field(node, "warn"), warn);
  setHidden(field(node, "warn"), !warn);

  setTime(node, "created", job.created_at, now);
  setTime(node, "started", job.started_at, now);
  setTime(node, "finished", job.finished_at, now);
  const duration = jobDuration(job, now);
  setHidden(field(node, "duration-row"), !duration);
  setText(field(node, "duration"), duration);

  // Rebuild buttons only when they change, so focus and hover survive polling.
  const actions = field(node, "actions");
  const key = JSON.stringify([state, jobDownloads(job.result, location.origin).map((l) => l.href)]);
  if (actions.dataset.key !== key) {
    const focused = actions.contains(document.activeElement) ? document.activeElement.dataset.action : null;
    actions.innerHTML = actionsHtml(job, id);
    actions.dataset.key = key;
    if (focused) (actions.querySelector("button, a") || field(node, "book"))?.focus({ preventScroll: true });
  }
  setHidden(actions, !actions.childElementCount);
}

function announce(text) {
  const live = document.getElementById("jobs-live");
  // Re-set on the next frame so repeated identical messages are still read.
  live.textContent = "";
  requestAnimationFrame(() => {
    live.textContent = text;
  });
}

function renderJobs(jobs) {
  const list = document.getElementById("jobs-list");
  const now = new Date();
  const seen = new Set();
  const changes = [];
  let previous = null;
  for (const job of jobs) {
    const id = jobId(job);
    seen.add(id);
    let node = jobNodes.get(id);
    if (!node) {
      node = createJobNode(id);
      jobNodes.set(id, node);
    }
    updateJobNode(node, job, now);
    // Keep server order (newest first) with minimal DOM moves.
    const expected = previous ? previous.nextSibling : list.firstChild;
    if (expected !== node) list.insertBefore(node, expected);
    previous = node;
    setHidden(node, !jobFilterMatches(job, currentFilter));

    const state = jobState(job);
    const before = jobSnapshots.get(id);
    if (!firstLoad && before !== state) {
      const title = job.book_title || job.result?.book_title || "";
      changes.push(`${jobTypeLabel(job.type)}${title ? ` („${title}”)` : ""}: ${before ? "" : "új feladat, "}${jobStateLabel(state).toLocaleLowerCase("hu")}`);
    }
    jobSnapshots.set(id, state);
  }
  for (const [id, node] of jobNodes) {
    if (!seen.has(id)) {
      node.remove();
      jobNodes.delete(id);
      jobSnapshots.delete(id);
    }
  }
  if (changes.length) announce(changes.slice(0, 3).join(". ") + (changes.length > 3 ? ` és további ${changes.length - 3} változás.` : "."));
  updateFilterCounts(jobs);
  updateEmptyState(jobs);
  firstLoad = false;
}

function updateFilterCounts(jobs) {
  for (const button of document.querySelectorAll("[data-filter]")) {
    const n = jobs.filter((j) => jobFilterMatches(j, button.dataset.filter)).length;
    setText(button.querySelector(".filter-count"), String(n));
  }
}

function updateEmptyState(jobs) {
  const empty = document.getElementById("jobs-empty");
  const visible = jobs.some((j) => jobFilterMatches(j, currentFilter));
  setHidden(empty, visible);
  if (visible) return;
  const titles = {
    active: ["Nincs futó feladat", "Most semmi nem fut. Az elkészült és a hibás feladatokat a többi szűrőnél találod."],
    done: ["Még nincs elkészült feladat", "Az elkészült generálások és exportok itt jelennek meg."],
    problem: ["Nincs hibás feladat", "Minden feladat rendben lefutott vagy még folyamatban van."],
    all: ["Még nincs feladat", "Nyiss meg egy könyvet, és indíts fejezethangot vagy exportot."],
  }[currentFilter];
  setText(empty.querySelector("h2"), titles[0]);
  setText(empty.querySelector("p"), titles[1]);
}

function setFilter(filter, focus = false) {
  currentFilter = ["active", "done", "problem", "all"].includes(filter) ? filter : "all";
  for (const button of document.querySelectorAll("[data-filter]"))
    button.setAttribute("aria-pressed", String(button.dataset.filter === currentFilter));
  try {
    localStorage.setItem(FILTER_KEY, currentFilter);
  } catch {
    /* private mode */
  }
  for (const job of lastJobs) setHidden(jobNodes.get(jobId(job)), !jobFilterMatches(job, currentFilter));
  updateEmptyState(lastJobs);
  if (focus) document.querySelector(`[data-filter="${currentFilter}"]`)?.focus();
}

async function loadJobs() {
  if (loadingJobs) return;
  loadingJobs = true;
  const message = document.getElementById("jobs-message");
  try {
    const data = await window.Auris.api("/api/jobs");
    lastJobs = Array.isArray(data) ? data : data.jobs || [];
    document.getElementById("jobs-loading")?.remove();
    renderJobs(lastJobs);
    setText(message, "");
    message.className = "jobs-message";
  } catch (e) {
    setText(message, `A feladatlista nem érhető el: ${e.message}`);
    message.className = "jobs-message is-error";
  } finally {
    loadingJobs = false;
  }
}

async function runJobAction(button) {
  const id = button.dataset.job,
    action = button.dataset.action;
  button.disabled = true;
  try {
    await window.Auris.api(`/api/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" });
    window.Auris.toast(action === "cancel" ? "Leállítás kérve." : "A feladat újraindult.", "ok");
    await loadJobs();
  } catch (error) {
    window.Auris.toast(error.message, "err");
    const message = document.getElementById("jobs-message");
    setText(message, error.message);
    message.className = "jobs-message is-error";
    button.disabled = false;
  }
}

function initJobs() {
  document.getElementById("jobs-list").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-job]");
    if (button) runJobAction(button);
  });
  document.getElementById("jobs-filters").addEventListener("click", (e) => {
    const button = e.target.closest("[data-filter]");
    if (button) setFilter(button.dataset.filter);
  });
  document.getElementById("jobs-refresh").addEventListener("click", loadJobs);
  let saved = null;
  try {
    saved = localStorage.getItem(FILTER_KEY);
  } catch {
    /* private mode */
  }
  setFilter(saved || "all");
  loadJobs();
  // Server events drive refreshes; the slow timer only covers missed events.
  let queued = null;
  const refreshSoon = () => {
    if (queued || document.hidden) return;
    queued = setTimeout(() => { queued = null; loadJobs(); }, 400);
  };
  if (window.Auris?.onEvent) window.Auris.onEvent("jobs", refreshSoon);
  setInterval(() => {
    if (!document.hidden) loadJobs();
  }, window.EventSource ? 30000 : 3000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadJobs();
  });
}

if (typeof document !== "undefined" && document.getElementById("jobs-list")) initJobs();

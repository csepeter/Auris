// Library page: book grid, import flow, metadata and removal dialogs.
// Pure helpers at the top are exported for node tests (tests/library_jobs.node.test.js).

/* ── Pure helpers ─────────────────────────────────────────────────────────── */

// FNV-1a 32-bit: stable, fast and good enough to spread titles over the hue wheel.
function hashString(value) {
  let hash = 0x811c9dc5;
  const text = String(value ?? "");
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  // Final avalanche (murmur3 fmix32) so short titles still spread well.
  hash ^= hash >>> 16;
  hash = Math.imul(hash, 0x85ebca6b) >>> 0;
  hash ^= hash >>> 13;
  hash = Math.imul(hash, 0xc2b2ae35) >>> 0;
  hash ^= hash >>> 16;
  return hash >>> 0;
}

// Deterministic typographic cover: hue, ornament variant and title size class.
function coverSpec(title) {
  const clean = String(title ?? "").trim();
  const hash = hashString(clean.toLocaleLowerCase("hu"));
  const length = [...clean].length;
  return {
    // 12 hue families 30° apart, with a small offset inside the family.
    hue: (hash % 12) * 30 + ((hash >>> 4) % 12),
    variant: (hash >>> 9) % 4,
    size: length <= 14 ? "l" : length <= 32 ? "m" : length <= 60 ? "s" : "xs",
  };
}

// Mirrors the server check in /api/import/confirm (experience_api.confirm_import).
// Returns true/false, or null when the settings could not be read.
function llmConfigured(settings) {
  if (!settings || typeof settings !== "object") return null;
  const provider = String(settings.llm_provider || "local").trim().toLowerCase();
  if (provider === "openai")
    return Boolean(settings.openai_api_key) && Boolean(String(settings.openai_model || "").trim());
  return Boolean(String(settings.llm_base_url || "").trim()) && Boolean(String(settings.llm_model || "").trim());
}

// Reading and audio progress of a book from its /api/books/<id>/chapters rows.
function bookStats(book, chapters) {
  const list = Array.isArray(chapters) ? chapters : [];
  const count = list.length || Number(book?.total_chapters) || 0;
  let readIndex = 0;
  if (book?.progress_chapter_id) {
    const at = list.findIndex((c) => c.id === book.progress_chapter_id);
    readIndex = at >= 0 ? at + 1 : 0;
  }
  const finished = book?.reading_state === "finished";
  if (finished) readIndex = count;
  let audioDone = 0,
    audioPartial = 0,
    fraction = 0;
  for (const c of list) {
    const total = Number(c.audio_total) || 0,
      ready = Number(c.audio_ready) || 0;
    if (total > 0 && ready >= total) {
      audioDone++;
      fraction += 1;
    } else if (ready > 0) {
      audioPartial++;
      fraction += total ? ready / total : 0;
    }
  }
  return {
    chapters: count,
    readIndex,
    readPct: count ? Math.round((readIndex / count) * 100) : 0,
    audioDone,
    audioPartial,
    audioPct: list.length ? Math.round((fraction / list.length) * 100) : 0,
  };
}

const ACTIVE_JOB_STATES = ["pending", "queued", "running"];

// Newest active job per book id (the jobs API returns newest first).
function activeJobsByBook(jobList) {
  const map = new Map();
  for (const job of Array.isArray(jobList) ? jobList : []) {
    if (!job || job.book_id == null || !ACTIVE_JOB_STATES.includes(job.state)) continue;
    if (!map.has(job.book_id)) map.set(job.book_id, job);
  }
  return map;
}

const LIBRARY_JOB_LABELS = {
  generate_chapter: "Fejezethang",
  generate_book: "Könyvhang",
  export_chapter: "Fejezetexport",
  export_book: "Könyvexport",
  reanalyze: "Szereplők újraelemzése",
  initial_analysis: "Szereplőelemzés",
  qa_chapter: "Minőségellenőrzés",
  voice_suggestions: "Hangjavaslatok",
  speaker_review: "Beszélők ellenőrzése",
};

if (typeof module === "object" && module.exports) {
  module.exports = { hashString, coverSpec, llmConfigured, bookStats, activeJobsByBook };
}

/* ── Page state ───────────────────────────────────────────────────────────── */

let libraryBooks = [],
  importPreview = null,
  importSettings = null,
  deletingBook = null,
  editingOriginalLanguage = "",
  setupCompleted = null,
  activeJobs = new Map(),
  lastRenderSignature = "";
const chapterStats = new Map(); // book id -> chapter rows (null when unavailable)
const staleStats = new Set(); // book ids whose rows are refetched, old rows stay visible
const $ = (id) => document.getElementById(id);
const esc = (value) => window.Auris.esc(value);
const icon = (name) => (window.Auris?.icon ? window.Auris.icon(name) : "");
const WELCOME_KEY = "auris.welcomeDismissed";
const STATE_LABELS = {
  new: "Még nem kezdtem",
  reading: "Folyamatban",
  finished: "Befejeztem",
};

async function api(url, options) {
  const r = await fetch(url, options);
  let d;
  try {
    d = await r.json();
  } catch {
    throw new Error("A szerver válasza nem olvasható. Próbáld újra.");
  }
  if (!r.ok) {
    const error = new Error(d.error || "A művelet nem sikerült.");
    error.data = d;
    throw error;
  }
  return d;
}
function post(url, data) {
  return api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}
function status(message, error = false) {
  $("import-status").textContent = message;
  $("import-status").className = "import-status" + (error ? " error" : "");
}
function effectiveState(b) {
  return (
    b.reading_state ||
    (b.last_read || b.progress_chapter_id ? "reading" : "new")
  );
}
function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the card simply shows again next time */
  }
}

/* ── Loading ──────────────────────────────────────────────────────────────── */

async function loadBooks() {
  try {
    const [books, jobList] = await Promise.all([
      api("/api/books?include=stats"),
      api("/api/jobs?state=active").catch(() => []),
    ]);
    libraryBooks = books;
    const previousActive = activeJobs;
    activeJobs = activeJobsByBook(jobList);
    // A book whose job just finished (or is running) gets fresh chapter stats.
    for (const id of new Set([...previousActive.keys(), ...activeJobs.keys()]))
      staleStats.add(id);
    const selected = $("library-collection").value;
    const collections = [...new Set(libraryBooks.map((b) => b.collection).filter(Boolean))].sort(
      (a, b) => a.localeCompare(b, "hu"),
    );
    $("library-collection").innerHTML =
      '<option value="">Mindegyik</option>' +
      collections.map((c) => `<option>${esc(c)}</option>`).join("");
    $("library-collection").value = collections.includes(selected) ? selected : "";
    updateWelcome();
    renderBooks();
    loadChapterStats();
  } catch (e) {
    lastRenderSignature = "";
    $("book-grid").innerHTML =
      `<p role="alert" class="library-error">${esc(e.message)} <button type="button" class="btn btn-ghost" onclick="loadBooks()">Újrapróbálás</button></p>`;
  }
}

// Chapter rows give reading position and audio readiness per book. Fetched
// lazily, four at a time, and cached until a job for the book changes.
let statsLoading = false;
async function loadChapterStats() {
  if (statsLoading) return;
  statsLoading = true;
  try {
    const queue = libraryBooks.filter((b) => !chapterStats.has(b.id) || staleStats.has(b.id));
    for (const book of queue) staleStats.delete(book.id);
    const worker = async () => {
      while (queue.length) {
        const book = queue.shift();
        try {
          // /api/books?include=stats already carries the per-chapter counts.
          chapterStats.set(book.id, Array.isArray(book.chapter_stats) && !staleStats.has(book.id)
            ? book.chapter_stats
            : await api(`/api/books/${book.id}/chapters`));
        } catch {
          chapterStats.set(book.id, null);
        }
        updateCardStats(book.id);
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);
  } finally {
    statsLoading = false;
  }
}

/* ── Welcome and continue cards ───────────────────────────────────────────── */

function updateWelcome() {
  const show =
    setupCompleted === false && libraryBooks.length === 0 && storageGet(WELCOME_KEY) !== "1";
  $("welcome-card").classList.toggle("hidden", !show);
}
function dismissWelcome() {
  storageSet(WELCOME_KEY, "1");
  $("welcome-card").classList.add("hidden");
  $("add-book-btn").focus();
}

function renderContinue() {
  const recent = libraryBooks
    .filter((b) => b.progress_chapter_id && effectiveState(b) !== "finished")
    .sort((a, b) => String(b.last_read).localeCompare(String(a.last_read)))[0];
  $("continue-card").classList.toggle("hidden", !recent);
  if (!recent) return;
  $("continue-card").innerHTML =
    `<div class="continue-text"><span class="eyebrow">Ahol abbahagytad</span><h2>${esc(recent.title)}</h2><p>${esc(recent.progress_chapter_title || "Mentett hely")}</p></div>` +
    `<a class="btn btn-primary" href="/reader/${recent.id}">${icon("play")}<span>Folytatom</span></a>`;
}

/* ── Book grid ────────────────────────────────────────────────────────────── */

function filteredBooks() {
  const query = $("library-search").value.trim().toLocaleLowerCase("hu"),
    state = $("library-state").value,
    collection = $("library-collection").value;
  const books = libraryBooks.filter(
    (b) =>
      `${b.title} ${b.author} ${b.series || ""} ${b.collection || ""}`
        .toLocaleLowerCase("hu")
        .includes(query) &&
      (state === "all" || effectiveState(b) === state) &&
      (!collection || b.collection === collection),
  );
  const sort = $("library-sort").value;
  books.sort((a, b) =>
    sort === "title" || sort === "author"
      ? String(a[sort] || "").localeCompare(String(b[sort] || ""), "hu")
      : String(b[sort === "added" ? "added_at" : "last_read"] || b.added_at).localeCompare(
          String(a[sort === "added" ? "added_at" : "last_read"] || a.added_at),
        ),
  );
  return books;
}

function typographicCover(book) {
  const spec = coverSpec(book.title);
  return (
    `<div class="typo-cover typo-v${spec.variant}" style="--cover-h:${spec.hue}" aria-hidden="true">` +
    `<span class="typo-rule"></span><span class="typo-title typo-${spec.size}">${esc(book.title)}</span>` +
    `<span class="typo-author">${esc(book.author || "")}</span></div>`
  );
}

function statsHtml(book) {
  const chapters = chapterStats.get(book.id);
  if (chapters === undefined)
    return `<p class="book-meter-loading">Állapot betöltése…</p>`;
  if (chapters === null)
    return `<p class="book-meter-loading">A fejezetállapot most nem érhető el.</p>`;
  const s = bookStats(book, chapters);
  const reading =
    book.reading_state === "finished"
      ? "Befejezve"
      : s.readIndex
        ? `${s.readIndex}. / ${s.chapters} fejezet`
        : "Még nem kezdted";
  let audio = "Még nincs hang";
  if (s.chapters && s.audioDone === s.chapters) audio = "Minden fejezet kész";
  else if (s.audioDone || s.audioPartial)
    audio = `${s.audioDone} / ${s.chapters} fejezet kész${s.audioPartial ? `, ${s.audioPartial} részben` : ""}`;
  return (
    `<div class="book-meter"><span class="meter-label">Olvasás</span><span class="meter-value">${esc(reading)}</span>` +
    `<span class="meter-bar" aria-hidden="true"><i style="width:${s.readPct}%"></i></span></div>` +
    `<div class="book-meter meter-audio"><span class="meter-label">Hang</span><span class="meter-value">${esc(audio)}</span>` +
    `<span class="meter-bar" aria-hidden="true"><i style="width:${s.audioPct}%"></i></span></div>`
  );
}

function noticeHtml(book) {
  const job = activeJobs.get(book.id);
  if (job) {
    const label = LIBRARY_JOB_LABELS[job.type] || "Feladat";
    const progress = job.total ? ` · ${job.done || 0}/${job.total}` : "";
    return `<a class="book-notice" href="/jobs">${icon("wave")}<span>${esc(label)} folyamatban${progress}</span></a>`;
  }
  if (["queued", "running"].includes(book.character_analysis_status))
    return `<a class="book-notice" href="/jobs">${icon("users")}<span>Szereplők elemzése folyamatban…</span></a>`;
  if (book.character_analysis_status === "failed")
    return `<p class="book-notice is-warn">${icon("alert")}<span>A szereplőelemzés nem sikerült</span></p>`;
  return "";
}

function bookCard(b) {
  const id = Number(b.id);
  const cover = b.cover_url
    ? `<img src="${esc(b.cover_url)}" alt="" loading="lazy">`
    : typographicCover(b);
  const readLabel = b.progress_chapter_id && effectiveState(b) !== "finished" ? "Folytatás" : "Olvasás";
  return (
    `<article class="book-card" data-id="${id}" aria-labelledby="book-title-${id}">` +
    `<a class="book-cover" href="/reader/${id}" tabindex="-1" aria-hidden="true">${cover}</a>` +
    `<span class="book-type-badge">${esc(b.file_type)}</span>` +
    `<div class="book-info">` +
    `<h2 class="book-title" id="book-title-${id}"><a href="/reader/${id}" data-focus="title-${id}">${esc(b.title)}</a></h2>` +
    `<div class="book-author">${esc(b.author || "Ismeretlen szerző")}</div>` +
    `<p class="book-progress-hint"><span class="book-state state-${effectiveState(b)}">${STATE_LABELS[effectiveState(b)]}</span> · ${Number(b.total_chapters) || 0} fejezet${b.series ? ` · ${esc(b.series)}` : ""}</p>` +
    `<div class="book-meters" data-stats="${id}">${statsHtml(b)}</div>` +
    `<div class="book-notices" data-notice="${id}">${noticeHtml(b)}</div>` +
    `</div>` +
    `<div class="book-actions" role="group" aria-label="${esc(b.title)}: műveletek">` +
    `<div class="book-action-row">` +
    `<a href="/reader/${id}" data-focus="read-${id}">${icon("book")}<span>${readLabel}</span></a>` +
    `<a href="/voice-studio/${id}" data-focus="voices-${id}">${icon("users")}<span>Hangok</span></a>` +
    `<a href="/qa/${id}" data-focus="qa-${id}">${icon("check")}<span>Ellenőrzés</span></a>` +
    `</div>` +
    `<a class="book-action-primary" href="/production/${id}" data-focus="production-${id}" title="Hangoskönyv készítése: generálás, ellenőrzés, export">${icon("factory")}<span>Produkció</span></a>` +
    `<div class="book-action-minor">` +
    `<button type="button" data-focus="details-${id}" onclick="openBookDetails(${id})">${icon("edit")}<span>Adatok</span></button>` +
    `<button type="button" class="del-btn" data-focus="delete-${id}" onclick="deleteBook(event,${id})" aria-label="${esc(b.title)} eltávolítása">Eltávolítás</button>` +
    `</div></div></article>`
  );
}

function renderBooks() {
  const books = filteredBooks();
  $("library-count").textContent = `${books.length} / ${libraryBooks.length} könyv`;
  renderContinue();
  updateFilterToggle();
  const signature = JSON.stringify([
    books,
    [...activeJobs].map(([k, j]) => [k, j.type, j.done, j.total]),
  ]);
  if (signature === lastRenderSignature) return;
  lastRenderSignature = signature;
  if (!books.length) {
    $("book-grid").innerHTML = libraryBooks.length
      ? `<div class="empty-library"><p>Nincs a szűrésnek megfelelő könyv.</p><p class="sub"><button type="button" class="btn btn-ghost" onclick="resetFilters()">Szűrők törlése</button></p></div>`
      : `<div class="empty-library"><p>Válaszd ki az első történeted.</p><p class="sub">EPUB, PDF, DOCX, TXT, PRC/MOBI vagy webcikk — a tartalmat import előtt ellenőrizheted.</p><p class="sub"><button type="button" class="btn btn-primary" onclick="$('file-input').click()">Könyv hozzáadása</button></p></div>`;
    return;
  }
  // Keep keyboard focus on the same control across re-renders (polling).
  const focusKey = document.activeElement?.closest?.("#book-grid") ? document.activeElement.dataset.focus : null;
  $("book-grid").innerHTML = books.map(bookCard).join("");
  if (focusKey)
    $("book-grid").querySelector(`[data-focus="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
}

function updateCardStats(id) {
  const book = libraryBooks.find((b) => b.id === id);
  const el = document.querySelector(`[data-stats="${id}"]`);
  if (book && el) el.innerHTML = statsHtml(book);
}

function resetFilters() {
  $("library-search").value = "";
  $("library-state").value = "all";
  $("library-collection").value = "";
  $("library-sort").value = "recent";
  renderBooks();
  $("library-search").focus();
}

function activeFilterCount() {
  return (
    ($("library-state").value !== "all") +
    ($("library-collection").value !== "") +
    ($("library-sort").value !== "recent")
  );
}
function updateFilterToggle() {
  const n = activeFilterCount();
  $("library-filter-count").textContent = n ? ` (${n})` : "";
}
function toggleFilters() {
  const tools = $("library-tools");
  const open = !tools.classList.contains("filters-open");
  tools.classList.toggle("filters-open", open);
  $("library-filter-toggle").setAttribute("aria-expanded", String(open));
}

/* ── Import ───────────────────────────────────────────────────────────────── */

async function handleFileChosen() {
  const file = this.files[0];
  this.value = "";
  if (!file) return;
  status("A dokumentum előnézetének előkészítése…");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("ocr", String($("import-ocr").checked));
  fd.append("ocr_language", $("import-ocr-language").value);
  fd.append("calibre", String($("import-calibre").checked));
  try {
    await showImportPreview(await api("/api/import/preview", { method: "POST", body: fd }));
    status("Az előnézet elkészült. Ellenőrizd a címet, a nyelvet és a szöveget.");
  } catch (e) {
    status(e.message, true);
  }
}
function openUrlDialog() {
  $("url-error").textContent = "";
  $("url-dialog").showModal();
  $("article-url").focus();
}
async function previewUrl() {
  const button = $("url-preview-btn");
  button.disabled = true;
  button.textContent = "Cikk letöltése…";
  $("url-error").textContent = "";
  try {
    const d = await post("/api/import/preview", { url: $("article-url").value });
    $("url-dialog").close();
    await showImportPreview(d);
  } catch (e) {
    $("url-error").textContent = e.message;
  } finally {
    button.disabled = false;
    button.textContent = "Előnézet";
  }
}

function renderDuplicate(duplicate) {
  const box = $("import-duplicate");
  $("import-allow-duplicate").checked = false;
  if (!duplicate) {
    box.hidden = true;
    return;
  }
  const id = Number(duplicate.id);
  $("import-duplicate-text").innerHTML =
    `Ez a tartalom már szerepel a könyvtárban: <strong>${esc(duplicate.title || "névtelen könyv")}</strong>.` +
    (Number.isFinite(id) && id > 0
      ? ` <a class="duplicate-open" href="/reader/${id}">Megnyitás</a>`
      : "");
  box.hidden = false;
}

async function showImportPreview(data) {
  importPreview = data;
  $("import-title").value = data.title;
  $("import-author").value = data.author;
  if (![...$("import-language").options].some((o) => o.value === data.language))
    $("import-language").add(new Option(data.language, data.language));
  $("import-language").value = data.language || "hu";
  $("import-sample").textContent = (data.import_note ? data.import_note + "\n\n" : "") + data.sample;
  $("import-source").textContent = data.source_url ? `Forrás: ${data.source_url}` : "";
  $("import-chapters-summary").textContent = `${data.chapters.length} fejezet`;
  $("import-chapters").innerHTML = data.chapters
    .map((c) => `<li>${esc(c.title)} · ${c.word_count} szó</li>`)
    .join("");
  renderDuplicate(data.duplicate);
  $("import-error").textContent = "";
  document.querySelector('[name="narration-mode"][value="single"]').checked = true;
  try {
    importSettings = await api("/api/settings");
  } catch {
    importSettings = null;
  }
  updateMultiAvailability();
  updateImportNote();
  updateConfirmState();
  $("import-dialog").showModal();
  $("import-title").focus();
}

// Character voices need a configured language model; say so before submit.
function updateMultiAvailability() {
  const ready = llmConfigured(importSettings);
  const input = document.querySelector('[name="narration-mode"][value="multi"]');
  const unavailable = ready === false;
  input.disabled = unavailable;
  input.closest(".narration-option").classList.toggle("is-disabled", unavailable);
  $("multi-unavailable").hidden = !unavailable;
  if (unavailable && input.checked)
    document.querySelector('[name="narration-mode"][value="single"]').checked = true;
}

function updateImportNote() {
  const multi = document.querySelector('[name="narration-mode"]:checked').value === "multi";
  $("import-model-note").textContent = !multi
    ? "Helyi felolvasás, nyelvimodell-hívás nélkül."
    : importSettings?.llm_provider === "openai"
      ? "OpenAI-elemzés: a könyv szövege az OpenAI szolgáltatásához kerül. Ez API-költséggel jár."
      : "A szereplőelemzés a beállított helyi nyelvi modellt használja. A felolvasásra az elemzés után kerülhet sor.";
}

function updateConfirmState() {
  const blocked = Boolean(importPreview?.duplicate) && !$("import-allow-duplicate").checked;
  $("confirm-import-btn").disabled = blocked;
  $("confirm-import-btn").title = blocked
    ? "Ez a tartalom már a könyvtárban van. Jelöld be a külön példány opciót, ha mégis hozzáadnád."
    : "";
}

function closeImportDialog() {
  $("import-dialog").close();
}
async function confirmImport() {
  if (!importPreview) return;
  const button = $("confirm-import-btn");
  button.disabled = true;
  button.textContent = "Hozzáadás…";
  try {
    const d = await post("/api/import/confirm", {
      token: importPreview.token,
      title: $("import-title").value,
      author: $("import-author").value,
      language: $("import-language").value,
      narration_mode: document.querySelector('[name="narration-mode"]:checked').value,
      allow_duplicate: $("import-allow-duplicate").checked,
    });
    $("import-dialog").close();
    status(
      `„${d.title}” hozzáadva. ${d.analysis_status === "queued" ? "A szereplőelemzés követhető a Feladatok oldalon." : "Megnyithatod és hallgathatod."}`,
    );
    await loadBooks();
  } catch (e) {
    if (e.data?.duplicate) {
      importPreview.duplicate = e.data.duplicate;
      renderDuplicate(e.data.duplicate);
    }
    $("import-error").textContent = e.message;
  } finally {
    button.textContent = "Hozzáadás";
    updateConfirmState();
  }
}

/* ── Book details, reanalysis and removal ─────────────────────────────────── */

async function openBookDetails(id) {
  // Prefill from the list row at once; the metadata request fills the rest.
  const cached = libraryBooks.find((book) => book.id === id);
  if (cached) {
    $("details-id").value = id;
    $("details-book-title").value = cached.title || "";
    $("details-author").value = cached.author || "";
    $("details-language").value = String(cached.language || "hu").trim().toLowerCase();
  }
  let b;
  try {
    b = await api(`/api/books/${id}/metadata`);
  } catch (error) {
    status(error.message, true);
    return;
  }
  $("details-id").value = id;
  $("details-book-title").value = b.title;
  $("details-author").value = b.author;
  editingOriginalLanguage = String(b.language || "").trim().toLowerCase();
  $("details-language").value = editingOriginalLanguage || "hu";
  $("details-series").value = b.series || "";
  $("details-series-index").value = b.series_index || "";
  $("details-publisher").value = b.publisher || "";
  $("details-published").value = b.published || "";
  $("details-description").value = b.description || "";
  $("details-collection").value = b.collection || "";
  $("details-state").value = effectiveState(b);
  $("details-analysis").textContent = b.character_analysis_message || "";
  $("details-error").textContent = "";
  $("details-chapter-picker").open = false;
  $("details-chapters").textContent = "Fejezetek betöltése…";
  $("reanalyze-selected").disabled = true;
  let source = null;
  try {
    const url = new URL(b.source_url);
    if (["https:", "http:"].includes(url.protocol)) source = url.href;
  } catch (_) {}
  $("details-source").classList.toggle("hidden", !source);
  $("details-source").href = source || "#";
  $("book-details").showModal();
  try {
    const chapters = await api(`/api/books/${id}/chapters`);
    if ($("details-id").value !== String(id)) return;
    $("details-chapters").replaceChildren();
    for (const chapter of chapters) {
      const label = document.createElement("label");
      label.className = "check-row";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = chapter.id;
      input.addEventListener("change", () => {
        $("reanalyze-selected").disabled = !document.querySelector("#details-chapters input:checked");
      });
      label.append(input, document.createTextNode(chapter.title));
      $("details-chapters").append(label);
    }
    if (!chapters.length) $("details-chapters").textContent = "Nincs feldolgozható fejezet.";
  } catch (e) {
    if ($("details-id").value === String(id)) $("details-chapters").textContent = e.message;
  }
}
async function saveBookDetails() {
  try {
    const language = $("details-language").value.trim().toLowerCase();
    if (
      language !== editingOriginalLanguage &&
      !window.confirm("A nyelv módosítása törli a könyv korábban létrehozott hangjait. Folytatod?")
    )
      return;
    const id = Number($("details-id").value);
    const result = await api(`/api/books/${id}/metadata`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: $("details-book-title").value,
        author: $("details-author").value,
        language,
        series: $("details-series").value,
        series_index: $("details-series-index").value,
        publisher: $("details-publisher").value,
        published: $("details-published").value,
        description: $("details-description").value,
        collection: $("details-collection").value,
        reading_state: $("details-state").value,
      }),
    });
    $("book-details").close();
    chapterStats.delete(id);
    await loadBooks();
    if (result.segments_cleared)
      status("A nyelv megváltozott; a hangok az új kiejtéssel készülnek el újra.");
  } catch (e) {
    $("details-error").textContent = e.message;
  }
}
async function reanalyzeBook(failedOnly, selectedOnly = false) {
  try {
    const chapterIds = Array.from(document.querySelectorAll("#details-chapters input:checked"), (input) =>
      Number(input.value),
    );
    if (selectedOnly && !chapterIds.length) throw new Error("Jelölj ki legalább egy fejezetet.");
    $("details-error").textContent = "";
    await post(`/api/books/${$("details-id").value}/reanalyze`, {
      failed_only: failedOnly,
      ...(selectedOnly ? { chapter_ids: chapterIds } : {}),
    });
    $("details-analysis").textContent = "Az újraelemzés elindult. A Feladatok oldalon követheted.";
  } catch (e) {
    $("details-error").textContent = e.message;
  }
}
function deleteBook(event, id) {
  event.stopPropagation();
  deletingBook = id;
  $("delete-book-name").textContent = libraryBooks.find((b) => b.id === id)?.title;
  $("delete-source").checked = false;
  $("delete-audio").checked = false;
  $("delete-error").textContent = "";
  $("delete-dialog").showModal();
}
async function confirmDelete() {
  try {
    const result = await post(`/api/books/${deletingBook}/remove`, {
      source: $("delete-source").checked,
      audio: $("delete-audio").checked,
    });
    $("delete-dialog").close();
    chapterStats.delete(deletingBook);
    if (result.warnings?.length) status(result.warnings.join(" "), true);
    await loadBooks();
  } catch (e) {
    $("delete-error").textContent = e.message;
  }
}

/* ── Bootstrap ────────────────────────────────────────────────────────────── */

function initLibrary() {
  for (const id of ["library-search", "library-state", "library-sort", "library-collection"])
    $(id).addEventListener("input", renderBooks);
  $("file-input").addEventListener("change", handleFileChosen);
  $("add-book-btn").addEventListener("click", () => $("file-input").click());
  $("library-filter-toggle").addEventListener("click", toggleFilters);
  $("welcome-dismiss").addEventListener("click", dismissWelcome);
  $("import-allow-duplicate").addEventListener("change", updateConfirmState);
  document
    .querySelectorAll('[name="narration-mode"]')
    .forEach((e) => e.addEventListener("change", updateImportNote));

  loadBooks();
  api("/api/setup/status")
    .then((s) => {
      setupCompleted = Boolean(s.completed);
      updateWelcome();
    })
    .catch(() => {});
  setInterval(() => {
    if (document.hidden) return;
    if (
      activeJobs.size ||
      libraryBooks.some((b) => ["queued", "running"].includes(b.character_analysis_status))
    )
      loadBooks();
  }, 4000);

  api("/api/import/tools")
    .then((tools) => {
      $("import-tools-status").textContent = `Tesseract: ${tools.ocr ? "elérhető" : "nincs telepítve"} · Nyelvek: ${tools.ocr_languages.join(", ") || "nincs"} · Calibre: ${tools.calibre ? "elérhető" : "nincs telepítve"}`;
      for (const lang of tools.ocr_languages) {
        if (!Array.from($("import-ocr-language").options).some((o) => o.value === lang))
          $("import-ocr-language").add(new Option(lang, lang));
      }
    })
    .catch((error) => {
      $("import-tools-status").textContent = error.message;
    });
}

if (typeof document !== "undefined" && document.getElementById("book-grid")) initLibrary();

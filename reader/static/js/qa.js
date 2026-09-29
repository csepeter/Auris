// Quality-control page: run checks, review flagged sentences, pick takes.
(() => {
  const BOOK_ID = window.BOOK_ID;
  const t = window.Auris.t;
  const FLAG_LABELS = new Proxy({}, { get: (_, flag) => t(`qa_flag.${String(flag)}`, String(flag)) });
  const STATUS_LABELS = new Proxy({}, { get: (_, key) => t(`qa.${String(key)}`, String(key)) });
  let filter = "problems";
  let segments = [];
  let pollTimer = null;
  const audio = document.getElementById("qa-audio");

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function chapterId() { return Number($("qa-chapter").value); }

  function setStatus(message, kind = "") {
    const el = $("qa-status");
    el.textContent = message;
    el.className = `qa-status ${kind}`;
  }

  function percent(value) {
    return value === null || value === undefined ? "–" : `${(value * 100).toFixed(1).replace(".", ",")}%`;
  }

  function renderSummary(summary) {
    const total = Object.entries(summary)
      .filter(([key]) => key !== "approved")
      .reduce((sum, [, value]) => sum + value, 0);
    $("qa-summary").innerHTML = ["ok", "warn", "fail", "unchecked"].map((key) => `
      <div class="qa-stat qa-stat-${key}"><span class="qa-stat-value">${summary[key] || 0}</span>
      <span class="qa-stat-label">${STATUS_LABELS[key]}</span></div>`).join("") + `
      <div class="qa-stat"><span class="qa-stat-value">${summary.approved || 0}/${total}</span>
      <span class="qa-stat-label">Jóváhagyva</span></div>`;
  }

  function visible(item) {
    if (filter === "all") return true;
    if (filter === "unapproved") return !item.approved;
    return item.status === "fail" || item.status === "warn";
  }

  function drawWaveform(canvas, url) {
    fetch(url).then((r) => r.arrayBuffer()).then((buffer) => {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      return ctx.decodeAudioData(buffer).then((decoded) => { ctx.close(); return decoded; });
    }).then((decoded) => {
      const data = decoded.getChannelData(0);
      const g = canvas.getContext("2d");
      const { width, height } = canvas;
      const step = Math.max(1, Math.floor(data.length / width));
      g.clearRect(0, 0, width, height);
      g.fillStyle = getComputedStyle(canvas).color;
      for (let x = 0; x < width; x += 1) {
        let peak = 0;
        for (let i = x * step; i < Math.min(data.length, (x + 1) * step); i += 1) {
          peak = Math.max(peak, Math.abs(data[i]));
        }
        const h = Math.max(1, peak * height);
        g.fillRect(x, (height - h) / 2, 1, h);
      }
    }).catch(() => {});
  }

  function renderList() {
    const shown = segments.filter(visible);
    if (!shown.length) {
      $("qa-list").innerHTML = `<p class="qa-empty">${segments.length
        ? "Ebben a nézetben nincs megjeleníthető szakasz."
        : "Ehhez a fejezethez még nincs ellenőrzés. Indítsd el a fenti gombbal."}</p>`;
      return;
    }
    $("qa-list").innerHTML = shown.map((item) => `
      <article class="qa-item qa-${item.status}${item.approved ? " is-approved" : ""}" data-index="${item.segment_index}">
        <div class="qa-item-head">
          <span class="qa-badge qa-badge-${item.status}">${STATUS_LABELS[item.status] || item.status}</span>
          <span class="qa-index">#${item.segment_index + 1}</span>
          ${item.character_name ? `<span class="qa-speaker">${esc(item.character_name)}</span>` : ""}
          <span class="qa-cer" title="Karakterhiba-arány: az elvárt és a visszahallott szöveg eltérése">CER ${percent(item.cer)}</span>
          ${(item.flags || []).map((flag) => `<span class="qa-flag">${esc(FLAG_LABELS[flag] || flag)}</span>`).join("")}
        </div>
        <p class="qa-text">${esc(item.text)}</p>
        ${item.heard ? `<p class="qa-heard"><span>Visszahallva:</span> ${esc(item.heard)}</p>` : ""}
        ${item.audio_url ? `<canvas class="qa-wave" width="600" height="40" data-src="${esc(item.audio_url)}"></canvas>` : ""}
        <div class="qa-actions">
          ${item.audio_url ? `<button class="btn btn-sm btn-ghost" data-action="play" data-url="${esc(item.audio_url)}" type="button">▶ Lejátszás</button>` : ""}
          <button class="btn btn-sm btn-ghost" data-action="take" type="button">↻ Új változat</button>
          <button class="btn btn-sm ${item.approved ? "btn-ghost" : "btn-primary"}" data-action="approve" data-approved="${item.approved ? 0 : 1}" type="button">${item.approved ? "Jóváhagyás visszavonása" : "✓ Jóváhagyás"}</button>
        </div>
        ${item.takes && item.takes.length > 1 ? `<div class="qa-takes" role="group" aria-label="Változatok">
          ${item.takes.map((take) => `<span class="qa-take${take.selected ? " is-selected" : ""}">
            <button class="btn btn-sm btn-ghost" data-action="play" data-url="${esc(take.audio_url)}" type="button">▶ ${take.take === 0 ? "Eredeti" : `${take.take}. változat`}</button>
            ${take.selected ? `<span class="qa-take-current">kiválasztva</span>` : `<button class="btn btn-sm btn-ghost" data-action="select" data-take="${take.take}" type="button">Ezt használom</button>`}
          </span>`).join("")}
        </div>` : ""}
      </article>`).join("");
    $("qa-list").querySelectorAll("canvas.qa-wave").forEach((canvas) => drawWaveform(canvas, canvas.dataset.src));
  }

  async function loadResults() {
    try {
      const data = await api(`/api/books/${BOOK_ID}/chapters/${chapterId()}/qa`);
      segments = data.segments || [];
      renderSummary(data.summary || {});
      renderList();
    } catch (error) {
      setStatus(error.message, "is-error");
    }
  }

  async function pollJob(jobId) {
    clearTimeout(pollTimer);
    try {
      const list = await api(`/api/jobs?book_id=${BOOK_ID}`);
      const job = (list.jobs || list).find((item) => item.id === jobId);
      if (!job) return;
      const bar = $("qa-progress");
      bar.classList.remove("hidden");
      const total = job.total || 0;
      $("qa-progress-bar").style.width = total ? `${Math.round((job.done / total) * 100)}%` : "8%";
      setStatus(job.message || job.state);
      if (["complete", "failed", "cancelled", "interrupted"].includes(job.state)) {
        bar.classList.add("hidden");
        $("qa-start").disabled = false;
        const result = job.result || {};
        if (job.state === "complete") {
          setStatus(`Kész. Újragenerált mondatok: ${result.regenerated || 0}, hibás maradt: ${result.failed || 0}.`, "is-ok");
        } else {
          setStatus(job.error || job.message || "Az ellenőrzés nem fejeződött be.", "is-error");
        }
        await loadResults();
        return;
      }
    } catch (error) {
      setStatus(error.message, "is-error");
    }
    pollTimer = setTimeout(() => pollJob(jobId), 1000);
  }

  async function startCheck() {
    $("qa-start").disabled = true;
    setStatus("Ellenőrzés indítása… Az első futás a beszédfelismerő modellt is betölti.");
    try {
      const data = await api(`/api/books/${BOOK_ID}/chapters/${chapterId()}/qa`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asr: $("qa-asr").checked, auto_regenerate: $("qa-regenerate").checked }),
      });
      pollJob(data.job_id);
    } catch (error) {
      $("qa-start").disabled = false;
      setStatus(error.message, "is-error");
    }
  }

  function row(label, value, ok) {
    const mark = ok === undefined ? "" : ok ? '<span class="qa-pass">✓</span>' : '<span class="qa-failmark">✕</span>';
    return `<tr><td>${label}</td><td>${value}</td><td>${mark}</td></tr>`;
  }

  function reportTable(title, r) {
    if (!r) return "";
    const c = r.acx_checks || {};
    const db = (v) => (v === undefined || v === null ? "–" : `${String(v).replace(".", ",")} dB`);
    return `<div class="qa-loudness-card"><h3>${title} ${r.acx_pass ? '<span class="qa-badge qa-badge-ok">ACX: megfelel</span>' : '<span class="qa-badge qa-badge-warn">ACX: nem felel meg</span>'}</h3>
      <table><tbody>
        ${row("Átlagos hangerő (RMS, −23…−18 dB)", db(r.rms_db), c.rms)}
        ${row("Csúcs (≤ −3 dB)", db(r.peak_db), c.peak)}
        ${row("Zajszint (≤ −60 dB)", db(r.noise_floor_db), c.noise_floor)}
        ${row("Integrált hangosság (EBU R128)", r.lufs !== undefined ? `${String(r.lufs).replace(".", ",")} LUFS` : "–")}
        ${row("True peak", db(r.true_peak_db))}
        ${row("Hossz (≤ 120 perc)", `${Math.round(r.duration_sec)} s`, c.length)}
      </tbody></table></div>`;
  }

  async function loudness() {
    const box = $("qa-loudness-report");
    box.classList.remove("hidden");
    box.innerHTML = "<p>Mérés… (a fejezet összefűzése és hangerő-elemzése)</p>";
    try {
      const data = await api(`/api/books/${BOOK_ID}/chapters/${chapterId()}/loudness`);
      box.innerHTML = reportTable("Nyers fejezet", data.raw) + reportTable("Mastering után (exportált hang)", data.mastered)
        + `<p class="qa-note">Az ACX a hangoskönyv-kiadók elterjedt technikai követelménye; mesterséges hangot az ACX maga nem fogad el, itt csak minőségi mércének használjuk.</p>`;
    } catch (error) {
      box.innerHTML = `<p class="is-error">${esc(error.message)}</p>`;
    }
  }

  async function onAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = button.closest(".qa-item");
    const index = item ? Number(item.dataset.index) : null;
    const base = `/api/books/${BOOK_ID}/chapters/${chapterId()}/segments/${index}`;
    try {
      if (button.dataset.action === "play") {
        audio.src = `${button.dataset.url}?t=${Date.now()}`;
        await audio.play();
      } else if (button.dataset.action === "take") {
        button.disabled = true;
        button.textContent = "Készül…";
        const data = await api(`${base}/takes`, { method: "POST" });
        audio.src = `${data.audio_url}?t=${Date.now()}`;
        audio.play().catch(() => {});
        await loadResults();
      } else if (button.dataset.action === "select") {
        await api(`${base}/select-take`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ take: Number(button.dataset.take) }),
        });
        await loadResults();
      } else if (button.dataset.action === "approve") {
        await api(`${base}/approve`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved: button.dataset.approved === "1" }),
        });
        await loadResults();
      }
    } catch (error) {
      setStatus(error.message, "is-error");
      button.disabled = false;
    }
  }

  const params = new URLSearchParams(location.search);
  if (params.get("chapter")) $("qa-chapter").value = params.get("chapter");
  $("qa-chapter").addEventListener("change", () => {
    $("qa-loudness-report").classList.add("hidden");
    loadResults();
  });
  $("qa-start").addEventListener("click", startCheck);
  $("qa-loudness").addEventListener("click", loudness);
  $("qa-list").addEventListener("click", onAction);
  document.querySelectorAll(".qa-filter button").forEach((button) => button.addEventListener("click", () => {
    filter = button.dataset.filter;
    document.querySelectorAll(".qa-filter button").forEach((b) => b.classList.toggle("is-active", b === button));
    renderList();
  }));
  // Resume watching a running check after a reload.
  api(`/api/jobs?book_id=${BOOK_ID}`).then((list) => {
    const running = (list.jobs || list).find((job) => job.type === "qa_chapter" && ["pending", "running"].includes(job.state));
    if (running) { $("qa-start").disabled = true; pollJob(running.id); }
  }).catch(() => {});
  loadResults();
})();

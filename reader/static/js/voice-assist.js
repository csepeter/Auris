// Voice Studio: language-model voice casting and speaker review.
(() => {
  const BOOK_ID = window.BOOK_ID;
  const $ = (id) => document.getElementById(id);
  const esc = window.Auris.esc;
  if (!$("assist-panel")) return;

  function setStatus(text, kind = "") {
    const el = $("assist-status");
    el.textContent = text;
    el.className = `assist-status ${kind}`;
  }

  function waitJob(jobId) {
    return window.Auris.watchJob(jobId, {
      onUpdate: (job) => setStatus(job.message || "Folyamatban…"),
    });
  }

  async function start(url, body) {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Az indítás nem sikerült.");
    return waitJob(data.job_id);
  }

  async function applySuggestion(suggestion) {
    const response = await fetch(`/api/books/${BOOK_ID}/characters/${suggestion.character_id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruct: suggestion.instruct, gender: suggestion.gender }),
    });
    if (!response.ok) throw new Error((await response.json()).error || "A mentés nem sikerült.");
  }

  function renderSuggestions(suggestions) {
    const list = $("assist-suggestions");
    if (!suggestions.length) { list.innerHTML = "<p class=\"muted\">Nincs javaslat.</p>"; return; }
    list.innerHTML = suggestions.map((s, i) => `
      <div class="assist-row"><div><strong>${esc(s.name)}</strong> <code>${esc(s.instruct)}</code>
      <p class="muted">${esc(s.description)}</p></div>
      <button type="button" class="btn btn-sm btn-ghost" data-apply="${i}">Alkalmazás</button></div>`).join("")
      + "<button type=\"button\" class=\"btn btn-sm btn-primary\" data-apply=\"all\">Összes alkalmazása</button>";
    list.onclick = async (event) => {
      const button = event.target.closest("[data-apply]");
      if (!button) return;
      const chosen = button.dataset.apply === "all" ? suggestions : [suggestions[Number(button.dataset.apply)]];
      try {
        for (const item of chosen) await applySuggestion(item);
        setStatus(`${chosen.length} hang alkalmazva. Az érintett mondatok újragenerálódnak.`, "is-ok");
        if (typeof window.loadCharacters === "function") window.loadCharacters();
        else location.reload();
      } catch (error) { setStatus(error.message, "is-error"); }
    };
  }

  $("assist-voices").addEventListener("click", async () => {
    setStatus("Hangjavaslat indítása… (a nyelvi modell idejére a beszédmotor felszabadul)");
    try {
      const job = await start(`/api/books/${BOOK_ID}/voice-suggestions`);
      if (job.state !== "complete") throw new Error(job.error || job.message);
      setStatus(job.message, "is-ok");
      renderSuggestions(job.result?.suggestions || []);
    } catch (error) { setStatus(error.message, "is-error"); }
  });

  $("assist-review").addEventListener("click", async () => {
    setStatus("Beszélők ellenőrzése… Fejezetenként egy-két percig is tarthat.");
    try {
      const job = await start(`/api/books/${BOOK_ID}/speaker-review`, {});
      if (job.state !== "complete") throw new Error(job.error || job.message);
      setStatus(job.message, "is-ok");
      const chapters = job.result?.chapters || [];
      $("assist-suggestions").innerHTML = chapters.map((c) => `
        <div class="assist-row"><div><strong>${esc(c.title)}</strong>: ${c.changed} javítás
        ${(c.corrections || []).slice(0, 8).map((x) => `<p class="muted">#${x.unit_index + 1} → ${esc(x.speaker || "narráció")}: ${esc(x.reason)}</p>`).join("")}</div></div>`).join("");
    } catch (error) { setStatus(error.message, "is-error"); }
  });
})();

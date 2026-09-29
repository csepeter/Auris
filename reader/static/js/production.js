// Production dashboard: status of the whole audiobook and its main actions.
(() => {
  const BOOK_ID = window.BOOK_ID;
  const { esc, api, toast } = window.Auris;
  const $ = (id) => document.getElementById(id);
  let watchedJob = null;

  function duration(seconds) {
    const s = Math.round(Number(seconds) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} ó ${m} p`;
    return m ? `${m} p ${s % 60} mp` : `${s} mp`;
  }

  function renderSteps(steps) {
    $('prod-steps').innerHTML = steps.map((step, i) => `
      <li class="prod-step${step.done ? ' is-done' : ''}">
        <span class="prod-step-mark" aria-hidden="true">${step.done ? Auris.icon('check') : i + 1}</span>
        <span class="prod-step-label">${esc(step.label)}${step.done ? '<span class="sr-only"> (kész)</span>' : ''}</span>
        ${step.note ? `<span class="prod-step-note">${esc(step.note)}</span>` : ''}
      </li>`).join('');
  }

  function renderChapters(chapters) {
    $('prod-chapters').innerHTML = chapters.map((c) => {
      const pct = c.segments ? Math.round((c.ready / c.segments) * 100) : 0;
      const qa = c.qa_checked
        ? `<span class="qa-mini ok">${c.qa.ok}</span><span class="qa-mini warn">${c.qa.warn}</span><span class="qa-mini fail">${c.qa.fail}</span>`
        : '<span class="muted">nincs</span>';
      return `<tr>
        <td class="num prod-col-num">${c.number}</td>
        <td class="prod-col-title"><a href="/reader/${BOOK_ID}?chapter=${c.id}">${esc(c.title)}</a><div class="muted prod-sub">${c.words} szó${c.speakers.length ? ' · ' + esc(c.speakers.slice(0, 4).join(', ')) : ''}</div></td>
        <td data-label="Hang"><div class="prod-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(c.title)} hangja"><div style="width:${pct}%"></div></div><span class="num prod-sub">${c.ready}/${c.segments}</span></td>
        <td class="num" data-label="Hossz">${c.duration_sec ? duration(c.duration_sec) : '–'}</td>
        <td data-label="Ellenőrzés">${qa}</td>
        <td class="prod-row-actions">
          <button class="btn btn-sm btn-ghost" type="button" data-generate="${c.id}"${pct === 100 ? ' disabled' : ''}>Generálás</button>
          <a class="btn btn-sm btn-ghost" href="/qa/${BOOK_ID}?chapter=${c.id}">Ellenőrzés</a>
        </td></tr>`;
    }).join('') || '<tr><td colspan="6">Nincs fejezet.</td></tr>';
  }

  function renderCast(data) {
    const section = $('prod-cast-section');
    if (data.book.single_narrator_mode) {
      $('prod-cast').innerHTML = '<p class="muted">Egy narrátoros mód: a teljes könyvet a narrátor olvassa fel. A narrátor hangját a Hangstúdióban állíthatod be.</p>';
      return;
    }
    const cast = data.characters;
    section.classList.toggle('hidden', !cast.length);
    const source = { reference: Auris.t('voice_source.reference'), description: Auris.t('voice_source.description') };
    $('prod-cast').innerHTML = cast.map((c) => `
      <div class="prod-cast-item${c.voice_source === 'reference' ? ' has-ref' : ''}">
        <strong>${esc(c.name)}</strong>
        <span class="muted">${c.lines} megszólalás · ${source[c.voice_source] || c.voice_source}</span>
      </div>`).join('');
  }

  async function load() {
    try {
      const data = await api(`/api/books/${BOOK_ID}/production`);
      renderSteps(data.steps);
      renderChapters(data.chapters);
      renderCast(data);
      const t = data.totals;
      $('stat-ready').textContent = `${t.ready}/${t.segments}`;
      $('stat-duration').textContent = duration(t.duration_sec);
      $('stat-qa').textContent = `${t.qa_checked} / ${t.qa_fail}`;
      const engine = data.engine.name ? Auris.t(`engine.${data.engine.name}`, data.engine.name) : '–';
      $('stat-engine').textContent = `${engine} · ${Auris.t(`engine_state.${data.engine.state}`, data.engine.state || '')}`;
      const running = data.active_jobs[0];
      if (running && !watchedJob) watch(running.id);
    } catch (error) {
      toast(error.message, 'err');
    }
  }

  function showJob(job) {
    const box = $('prod-job');
    box.classList.remove('hidden');
    $('prod-job-text').textContent = `${job.message || job.state} ${job.total ? `(${job.done}/${job.total})` : ''}`;
    $('prod-job-bar').style.width = job.total ? `${Math.round((job.done / job.total) * 100)}%` : '10%';
  }

  async function watch(jobId) {
    watchedJob = jobId;
    for (;;) {
      let job;
      try { job = await api(`/api/jobs/${jobId}`); } catch (_) { break; }
      showJob(job);
      if (['complete', 'failed', 'cancelled', 'interrupted'].includes(job.state)) {
        const r = job.result || {};
        const links = [];
        if (r.download) links.push(`<a href="${esc(r.download)}" download>Letöltés</a>`);
        $('prod-job-links').innerHTML = links.join(' ') + ' <a href="/jobs">Feladatok</a>';
        if (job.state === 'complete') toast('A feladat elkészült.', 'ok');
        else toast(job.error || job.message || 'A feladat nem sikerült.', 'err');
        watchedJob = null;
        load();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    watchedJob = null;
  }

  async function start(url, body) {
    try {
      const data = await api(url, { method: 'POST', body: body || {} });
      if (data.job_id) watch(data.job_id);
    } catch (error) {
      toast(error.message, 'err');
    }
  }

  $('prod-generate').addEventListener('click', () => start(`/api/books/${BOOK_ID}/generate`));
  document.querySelector('.prod-export').addEventListener('click', (event) => {
    const button = event.target.closest('[data-export]');
    if (!button) return;
    const kind = button.dataset.export;
    const credits = $('prod-credits').checked;
    const body = {
      audio_fmt: kind === 'epub3' || kind === 'acx' ? 'mp3' : 'm4b', sub_fmt: 'none', chapters: 'all',
      package: kind === 'm4b' ? 'none' : kind, intro: credits, outro: credits, sample: kind === 'acx',
    };
    start(`/api/books/${BOOK_ID}/export/chapterwise`, body);
  });
  $('prod-chapters').addEventListener('click', (event) => {
    const button = event.target.closest('[data-generate]');
    if (!button) return;
    start(`/api/books/${BOOK_ID}/generate`, { chapter_ids: [Number(button.dataset.generate)] });
  });
  // Chapter table follows server events instead of polling.
  let pending = null;
  Auris.onEvent?.('jobs', (data) => {
    const ours = [...(data.changed || []), ...(data.finished || [])].some((job) => job.book_id === BOOK_ID);
    if (!ours || pending) return;
    pending = setTimeout(() => { pending = null; load(); }, 1200);
  });
  load();
})();

// Reader: select a word → "Kiejtés" → listen with a replacement → save a rule.
(() => {
  const content = document.getElementById('chapter-content');
  if (!content || !window.BOOK_ID) return;
  const BOOK_ID = window.BOOK_ID;

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'pron-trigger hidden';
  trigger.textContent = 'Kiejtés';
  trigger.setAttribute('aria-label', 'Kiejtés javítása a kijelölt szóhoz');
  document.body.appendChild(trigger);

  const dialog = document.createElement('div');
  dialog.className = 'pron-dialog hidden';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'false');
  dialog.setAttribute('aria-labelledby', 'pron-title');
  dialog.innerHTML = `
    <div class="pron-head"><strong id="pron-title">Kiejtés javítása</strong>
      <button type="button" class="btn btn-sm btn-ghost" data-pron="close" aria-label="Bezárás">×</button></div>
    <label>Írott alak<input type="text" id="pron-source" class="text-input" maxlength="100"></label>
    <label>Így hangozzon el<input type="text" id="pron-replacement" class="text-input" maxlength="200" placeholder="pl. Vosington"></label>
    <p class="pron-ipa" id="pron-ipa" aria-live="polite"></p>
    <div class="pron-scope">
      <label><input type="radio" name="pron-scope" value="book" checked> Csak ebben a könyvben</label>
      <label><input type="radio" name="pron-scope" value="global"> Minden könyvben</label>
    </div>
    <p class="muted pron-note">Egyszavas szabály a ragozott alakokra is érvényes (pl. -nak, -val).</p>
    <div class="pron-actions">
      <button type="button" class="btn btn-sm btn-ghost" data-pron="listen">▶ Meghallgatás</button>
      <button type="button" class="btn btn-sm btn-primary" data-pron="save">Mentés</button>
    </div>
    <p class="pron-status" id="pron-status" role="status" aria-live="polite"></p>`;
  document.body.appendChild(dialog);
  const audio = new Audio();
  let sentence = '';

  const $ = (id) => document.getElementById(id);
  const status = (text, kind = '') => { const el = $('pron-status'); el.textContent = text; el.className = `pron-status ${kind}`; };

  function hideTrigger() { trigger.classList.add('hidden'); }

  content.addEventListener('mouseup', () => {
    const selection = window.getSelection();
    const text = selection ? selection.toString().trim() : '';
    if (!text || text.length > 60 || text.split(/\s+/).length > 4 || !selection.rangeCount) {
      hideTrigger();
      return;
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    const node = selection.anchorNode?.parentElement?.closest('.sentence');
    sentence = node ? node.textContent.trim() : text;
    trigger.dataset.text = text.replace(/^[„"«»(]+|[”"«»),.;:!?…]+$/gu, '');
    trigger.style.left = `${Math.round(rect.left + window.scrollX + rect.width / 2)}px`;
    trigger.style.top = `${Math.round(rect.top + window.scrollY - 36)}px`;
    trigger.classList.remove('hidden');
  });
  document.addEventListener('mousedown', (event) => {
    if (event.target !== trigger && !dialog.contains(event.target)) hideTrigger();
  });

  async function loadIpa(text) {
    $('pron-ipa').textContent = '';
    try {
      const data = await fetch(`/api/pronunciation/ipa?text=${encodeURIComponent(text)}`).then(r => r.json());
      if (data.ipa) $('pron-ipa').textContent = `Jelenlegi kiejtés (IPA): /${data.ipa}/`;
    } catch (_) { /* optional */ }
  }

  trigger.addEventListener('click', () => {
    hideTrigger();
    $('pron-source').value = trigger.dataset.text || '';
    $('pron-replacement').value = '';
    status('');
    dialog.classList.remove('hidden');
    $('pron-replacement').focus();
    loadIpa($('pron-source').value);
  });

  dialog.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-pron]')?.dataset.pron;
    if (!action) return;
    if (action === 'close') { dialog.classList.add('hidden'); return; }
    const source = $('pron-source').value.trim();
    const replacement = $('pron-replacement').value.trim();
    if (!source || !replacement) { status('Add meg az írott és a kiejtett alakot.', 'is-error'); return; }
    if (action === 'listen') {
      status('Felolvasás…');
      try {
        const response = await fetch('/api/pronunciation/listen', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({book_id: BOOK_ID, text: sentence || source, source, replacement}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'A felolvasás nem sikerült.');
        audio.src = data.audio_url;
        await audio.play();
        status(`Így hangzik: ${data.spoken}`);
      } catch (error) { status(error.message, 'is-error'); }
    } else if (action === 'save') {
      const scope = document.querySelector('input[name="pron-scope"]:checked')?.value;
      try {
        const response = await fetch('/api/pronunciation', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({book_id: scope === 'global' ? null : BOOK_ID, source, replacement}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'A mentés nem sikerült.');
        status('Elmentve. Az érintett mondatok a következő lejátszáskor újragenerálódnak.', 'is-ok');
      } catch (error) { status(error.message, 'is-error'); }
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !dialog.classList.contains('hidden')) dialog.classList.add('hidden');
  });
})();

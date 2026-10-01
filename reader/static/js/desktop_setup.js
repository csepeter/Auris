(() => {
  const form = document.querySelector('#setup-form');
  const start = document.querySelector('#start');
  const gpu = document.querySelector('#gpu');
  const progress = document.querySelector('#setup-progress');
  const error = document.querySelector('#setup-error');
  const done = document.querySelector('#setup-done');
  const restart = document.querySelector('#restart');
  let polling = false;
  form.addEventListener('change', () => {
    if (gpu) {
      gpu.disabled = form.elements.engine.value !== 'omnivoice';
      if (gpu.disabled) gpu.checked = false;
    }
  });
  async function request(url, body) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'A kérés nem sikerült.');
    return result;
  }
  function showError(message) { error.textContent = message; error.hidden = false; start.disabled = false; }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      while (true) {
        const response = await fetch('/api/desktop/status', {cache: 'no-store'});
        if (!response.ok) throw new Error('A letöltés állapota nem érhető el.');
        const state = await response.json();
        if (state.state === 'idle') break;
        progress.hidden = false;
        document.querySelector('#progress-message').textContent = state.message;
        document.querySelector('#progress-bar').value = state.progress;
        document.querySelector('#progress-detail').textContent = state.file_total
          ? `${(state.file_bytes / 1048576).toFixed(1)} / ${(state.file_total / 1048576).toFixed(1)} MB az aktuális fájlból`
          : 'A nagy fájlok letöltése több percig is tarthat. Az ablakot hagyd nyitva.';
        if (state.state === 'done') {
          form.hidden = true; done.hidden = false;
          document.querySelector('#progress-detail').textContent = 'A szükséges fájlok a számítógépeden vannak.';
          restart.hidden = !state.restart_required;
          document.querySelector('#open-library').hidden = state.restart_required;
          break;
        }
        if (state.state === 'error') { showError(state.message); break; }
        start.disabled = true;
        await new Promise(resolve => setTimeout(resolve, 800));
      }
    } catch (e) { showError(e.message); }
    finally { polling = false; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden = true; done.hidden = true; start.disabled = true;
    try {
      await request('/api/desktop/setup', {engine: form.elements.engine.value, gpu: Boolean(gpu && gpu.checked)});
      poll();
    } catch (e) { showError(e.message); }
  });
  restart.addEventListener('click', async () => {
    restart.disabled = true;
    try { await request('/api/desktop/restart', {}); }
    catch (e) { restart.disabled = false; showError(e.message); }
  });
  poll();
})();

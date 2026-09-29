// Shared browser helpers: escaping, JSON requests, accessible toasts, theme.
(function exposeAurisCommon(root) {
  const THEMES = ['night', 'sepia', 'paper', 'amoled'];

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  async function api(url, options = {}) {
    const init = { ...options };
    if (init.body && typeof init.body !== 'string' && !(init.body instanceof FormData)) {
      init.body = JSON.stringify(init.body);
      init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
    }
    const response = await fetch(url, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data.error?.message || data.error || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return data;
  }

  function region() {
    let el = document.getElementById('toast-region');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast-region';
      el.className = 'toast-region';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    return el;
  }

  function toast(message, kind = '', ms = 3500) {
    const item = document.createElement('div');
    item.className = `toast-item${kind === 'err' || kind === 'error' ? ' is-error' : kind === 'ok' ? ' is-ok' : ''}`;
    item.textContent = message;
    region().appendChild(item);
    setTimeout(() => item.remove(), ms);
  }

  function applyTheme(theme) {
    const html = document.documentElement;
    THEMES.forEach((t) => html.classList.remove(`theme-${t}`));
    if (theme && theme !== 'night') html.classList.add(`theme-${theme}`);
  }

  // Shared UI strings from static/i18n/hu.json: t('job.export_book').
  function t(key, fallback = '', vars = null) {
    const table = (typeof window !== 'undefined' && window.AURIS_I18N) || {};
    let text = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : (fallback || key);
    if (vars) Object.entries(vars).forEach(([k, v]) => { text = text.split(`{${k}}`).join(String(v)); });
    return text;
  }

  const DONE_STATES = ['complete', 'failed', 'cancelled', 'interrupted'];

  // Follow one job to its end. Progress arrives through the shared server
  // event stream (/api/events); a slow fallback request covers a missed event
  // or a browser without EventSource. Resolves with the final job, or null
  // when isActive() turns false first.
  function watchJob(jobId, { url = `/api/jobs/${jobId}`, onUpdate = null, isActive = null, fallbackMs = 10000 } = {}) {
    return new Promise((resolve) => {
      const auris = root && root.Auris;
      const live = Boolean(root && root.EventSource && auris && auris.onEvent);
      const interval = live ? fallbackMs : Math.min(fallbackMs, 2000);
      let done = false;
      let busy = false;
      let again = false;
      let timer = null;
      let off = null;
      const stop = (value) => {
        done = true;
        clearTimeout(timer);
        if (off) off();
        resolve(value);
      };
      async function refresh() {
        if (done) return;
        if (isActive && !isActive()) { stop(null); return; }
        if (busy) { again = true; return; }
        busy = true;
        let job = null;
        try {
          job = await fetch(url).then((r) => r.json());
          if (job.error && !job.state) job.state = 'failed';
        } catch (_) { /* retried by the fallback timer */ }
        busy = false;
        if (done) return;
        if (job) {
          if (onUpdate) { try { onUpdate(job); } catch (_) { /* keep watching */ } }
          if (DONE_STATES.includes(job.state)) { stop(job); return; }
        }
        if (again) { again = false; refresh(); return; }
        clearTimeout(timer);
        timer = setTimeout(refresh, interval);
      }
      if (live) {
        off = auris.onEvent('jobs', (data) => {
          const seen = [...(data.changed || []), ...(data.finished || [])];
          if (seen.some((job) => job.id === jobId)) refresh();
        });
      }
      refresh();
    });
  }

  const api_ = { esc, api, toast, applyTheme, t, watchJob, DONE_STATES, THEMES };
  if (typeof module === 'object' && module.exports) module.exports = api_;
  if (root) root.Auris = api_;
})(typeof window !== 'undefined' ? window : null);

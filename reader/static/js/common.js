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

  const api_ = { esc, api, toast, applyTheme, t, THEMES };
  if (typeof module === 'object' && module.exports) module.exports = api_;
  if (root) root.Auris = api_;
})(typeof window !== 'undefined' ? window : null);

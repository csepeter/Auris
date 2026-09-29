// Auris reader — search and bookmarks. Classic script sharing globals with reader.js;
// loaded after reader.js by templates/reader.html.

// ── Whole-book search ────────────────────────────────────────────────────────

function openBookSearch() {
  const overlay = document.getElementById('book-search-overlay');
  openModal(overlay, document.getElementById('book-search-input'));
}

function closeBookSearch() {
  closeModal(document.getElementById('book-search-overlay'));
}

async function searchBook(query) {
  const status = document.getElementById('book-search-status');
  const list = document.getElementById('book-search-results');
  const normalized = String(query || '').trim();
  if (normalized.length < 2) {
    status.textContent = 'Adj meg legalább két karaktert.';
    list.replaceChildren();
    return;
  }

  status.textContent = 'Keresés…';
  list.replaceChildren();
  try {
    const response = await fetch(`/api/books/${BOOK_ID}/search?q=${encodeURIComponent(normalized)}`);
    const results = await response.json();
    if (!response.ok) throw new Error(results.error || 'A keresés nem sikerült.');
    status.textContent = results.length ? `${results.length} találat` : 'Nincs találat.';
    results.forEach(result => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'search-result';
      const title = document.createElement('span');
      title.className = 'search-result-title';
      title.textContent = result.chapter_title || 'Névtelen fejezet';
      const excerpt = document.createElement('span');
      excerpt.className = 'search-result-excerpt';
      excerpt.textContent = result.excerpt || '';
      button.append(title, excerpt);
      button.addEventListener('click', () => {
        closeBookSearch();
        openChapter(Number(result.chapter_id), {
          resumePosition: Number(result.segment_index) || 0,
          persistCurrent: true,
          persistOpened: true,
          highlightOnLoad: true,
        });
      });
      list.appendChild(button);
    });
  } catch (error) {
    status.textContent = error.message;
  }
}

document.getElementById('book-search-btn').addEventListener('click', openBookSearch);
document.getElementById('book-search-close').addEventListener('click', closeBookSearch);
document.getElementById('book-search-overlay').addEventListener('click', event => {
  if (event.target === event.currentTarget) closeBookSearch();
});
document.getElementById('book-search-form').addEventListener('submit', event => {
  event.preventDefault();
  searchBook(document.getElementById('book-search-input').value);
});

// ── Bookmarks ─────────────────────────────────────────────────────────────────

let _bookmarks = [];

async function loadBookmarks() {
  _bookmarks = await fetch(`/api/books/${BOOK_ID}/bookmarks`).then(r => r.json());
  renderBookmarks();
}

function renderBookmarks() {
  const list = document.getElementById('bookmark-list');
  if (!_bookmarks.length) {
    list.innerHTML = '<div style="padding:16px;font-size:.8rem;color:var(--text3);font-style:italic">Még nincs könyvjelző.</div>';
    return;
  }
  list.innerHTML = _bookmarks.map(bm => `
    <div class="bookmark-item">
      <button class="bookmark-goto" type="button" onclick="gotoBookmark(${bm.chapter_id}, ${bm.segment_index})">
        <span class="bookmark-text">${esc(bm.text_excerpt || bm.label || '(nincs részlet)')}</span>
        <span class="bookmark-loc">${esc(bm.chapter_title || '')} &middot; ${bm.segment_index + 1}. szakasz</span>
      </button>
      <button class="bookmark-del" aria-label="Könyvjelző törlése" onclick="removeBookmark(event,${bm.id})">&times;</button>
    </div>`).join('');
}

async function addBookmark() {
  if (!currentChapterId) { showToast('Előbb nyiss meg egy fejezetet.'); return; }
  const seg = segments[currentSegIdx];
  const excerpt = seg ? seg.text.slice(0, 120) : '';
  const r = await fetch(`/api/books/${BOOK_ID}/bookmarks`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      chapter_id:    currentChapterId,
      segment_index: currentSegIdx,
      text_excerpt:  excerpt,
    }),
  });
  if (r.ok) {
    showToast('Könyvjelző hozzáadva.');
    loadBookmarks();
    const btn = document.getElementById('bookmark-btn');
    btn.textContent = '★';
    setTimeout(() => { btn.textContent = '☆'; }, 1500);
  }
}

async function removeBookmark(e, id) {
  e.stopPropagation();
  await fetch(`/api/books/${BOOK_ID}/bookmarks/${id}`, { method: 'DELETE' });
  loadBookmarks();
}

function gotoBookmark(chapterId, segIdx) {
  if (chapterId !== currentChapterId) {
    openChapter(chapterId, {
      resumePosition: segIdx,
      persistCurrent: true,
      persistOpened: true,
      highlightOnLoad: true,
    });
  } else {
    jumpTo(segIdx);
  }
}

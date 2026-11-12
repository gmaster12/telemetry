/* Greystar Telemetry: the shared search box. One field with a selector for what to look for:

     Movies & shows   Seerr, on the Requests page (#/requests/search?q=...)
     Comics           the Greystar media site in the Books & comics frame: its results page,
     Ebooks             /find?type=<kind>&q=, which lists what the library has first and what can be
     Audiobooks         requested second (Glenn's pick C, Nov 12 2026: no "In our library / Request new" switch)
     All books        every book library at once (no catalog)

   The media site's own box (src/components/UnifiedSearch.tsx in gmaster12/media) uses the same kinds, words
   and routes, so a member sees one box everywhere. Keep the two in step.

   Kinds follow what the person may use: Movies & shows needs the `requests` section; the book kinds need the
   `books` section, Books & comics turned on, and a Plex sign-in (a local admin login is not a Plex account, so
   the media site could not tell who it is). With one kind left, the selector becomes a plain label.

   The last kind picked is remembered in this browser (`gs-usearch-kind`). The Requests page always starts on
   Movies & shows. Book searches go to the frame with window.gsBooks.open(path); books.js sends the frame there
   without reloading it, so an audiobook keeps playing.

   The media site's own box (the one inside the frame) also suggests titles as you type; this box cannot, because
   the media site's library and catalog are only reachable from inside the frame, so Enter opens the results page.

   window.gsUSearch.create({ variant, kind, live, onLive }) -> { el, input, kind(), setKind(k), setValue(q), refresh() }
     variant  'page' (Requests page header), 'door' (viewer home door), 'hero' (Requests hub)
     live     for Movies & shows, call onLive(q) as the person types (the Requests page searches as you type);
              book kinds always wait for Search or Enter, so ComicVine and the book catalog are not hit on
              every keystroke. */
(function () {
  'use strict';

  var KEY = 'gs-usearch-kind';
  var svgNS = 'http://www.w3.org/2000/svg';
  var PATHS = {
    film: ['M4 5h16v14H4z', 'M8 5v14M16 5v14M4 9.5h4M4 14.5h4M16 9.5h4M16 14.5h4'],
    comic: ['M12 6.5C10 5 7 4.5 3.5 5v13.5c3.5-.5 6.5 0 8.5 1.5 2-1.5 5-2 8.5-1.5V5C17 4.5 14 5 12 6.5Z', 'M12 6.5V20'],
    ebook: ['M6 3.5h12A1.5 1.5 0 0 1 19.5 5v14a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5Z', 'M9 8h6M9 12h6M9 16h3'],
    audio: ['M4 14v-2a8 8 0 0 1 16 0v2', 'M4 14h3v6H5a1 1 0 0 1-1-1v-5Z', 'M20 14h-3v6h2a1 1 0 0 0 1-1v-5Z'],
    stack: ['M12 3.5 3.5 8 12 12.5 20.5 8Z', 'M3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16'],
    search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z', 'M21 21l-4.3-4.3'],
    chev: ['m6 9 6 6 6-6']
  };
  var KINDS = {
    video: { label: 'Movies & shows', hint: 'Find it or request it', icon: 'film' },
    comics: { label: 'Comics', hint: 'Series and issues', icon: 'comic' },
    ebook: { label: 'Ebooks', hint: 'Title, author or series', icon: 'ebook' },
    audiobook: { label: 'Audiobooks', hint: 'Title, author or narrator', icon: 'audio' },
    books: { label: 'All books', hint: 'Search only, every book library', icon: 'stack' }
  };
  var ORDER = ['video', 'comics', 'ebook', 'audiobook', 'books'];
  var instances = [];

  function icon(name, size) {
    var s = document.createElementNS(svgNS, 'svg');
    s.setAttribute('width', size || 18); s.setAttribute('height', size || 18); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.7');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    PATHS[name].forEach(function (d) { var p = document.createElementNS(svgNS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  var el = window.gsUI.el;   // shared helpers (ui.js)


  function can(section) { return !!(window.dash && window.dash.can && window.dash.can(section)); }

  /** Kinds this person may search, in menu order. */
  function kinds() {
    var out = [];
    if (can('requests')) out.push('video');
    if (can('books') && window.gsBooks && window.gsBooks.usable && window.gsBooks.usable()) out.push('comics', 'ebook', 'audiobook', 'books');
    return out;
  }

  /** The media site's in-app path for a book search (same as routeFor in the media site's UnifiedSearch): its
   *  results page with words, the kind's library page without. */
  function bookPath(kind, q) {
    q = (q || '').trim();
    if (q) return '/find?type=' + kind + '&q=' + encodeURIComponent(q);
    return kind === 'comics' ? '/library' : kind === 'ebook' ? '/ebooks' : kind === 'audiobook' ? '/audiobooks' : '/search';
  }

  function saved() {
    try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function remember(k) {
    try { window.localStorage.setItem(KEY, k); } catch (e) { /* storage blocked */ }
  }

  function placeholder(kind) {
    // A phone has room for a few words only.
    var phone = window.gsUI.isPhone();
    if (kind === 'video') return phone ? 'Movies, series, people' : 'Search movies, series and people';
    if (kind === 'books') return phone ? 'Comics, ebooks, audiobooks' : 'Search comics, ebooks and audiobooks';
    if (kind === 'comics') return 'Find a comic series';
    if (kind === 'ebook') return phone ? 'Ebook title or author' : 'Find an ebook by title or author';
    return phone ? 'Title, author or narrator' : 'Find an audiobook by title, author or narrator';
  }

  /** Go to the right page for a search. */
  function go(kind, q) {
    q = (q || '').trim();
    if (kind === 'video') {
      window.location.hash = '#/requests' + (q ? '/search?q=' + encodeURIComponent(q) : '');
      return;
    }
    if (window.gsBooks && window.gsBooks.open) window.gsBooks.open(bookPath(kind, q));
  }

  function create(opts) {
    opts = opts || {};
    var variant = opts.variant || 'page';
    var state = { kind: null, open: false };
    var liveTimer = null;

    var input = el('input', { type: 'search', class: 'us-input', autocomplete: 'off' });
    var selBtn = el('button', { type: 'button', class: 'us-selbtn', 'aria-haspopup': 'menu', 'aria-expanded': 'false' });
    var selText = el('span', { class: 'us-seltext' });
    var menu = el('div', { class: 'us-menu', role: 'menu', 'aria-label': 'What are you looking for', hidden: true });
    var sel = el('div', { class: 'us-sel' }, selBtn, selText, menu);
    var goBtn = el('button', { type: 'submit', class: 'us-go', text: 'Search' });
    var form = el('form', { class: 'us us--' + variant, role: 'search' },
      sel, el('label', { class: 'us-field' }, icon('search', 18), input), goBtn);

    function list() { var k = kinds(); return k.length ? k : ['video']; }

    function paint() {
      var ks = list();
      if (ks.indexOf(state.kind) < 0) state.kind = ks[0];
      var k = KINDS[state.kind];
      var single = ks.length < 2;
      selBtn.hidden = single;
      selText.hidden = !single;
      selBtn.replaceChildren(icon(k.icon), el('span', { class: 'us-sellabel', text: k.label }), icon('chev', 16));
      selBtn.setAttribute('aria-label', 'Search in: ' + k.label);
      selText.replaceChildren(icon(k.icon), document.createTextNode(k.label));
      var ph = placeholder(state.kind);
      input.placeholder = ph;
      input.setAttribute('aria-label', ph);
      form.dataset.kind = state.kind;
    }

    // On a phone the menu is the shared bottom sheet (ui.js): it moves to the end of the page while open, so no
    // card around the box can clip it, with a dimmed page behind it.
    var detach = null, scrim = null;
    function close(focusBtn) {
      if (!state.open) return;
      state.open = false;
      menu.hidden = true;
      selBtn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('mousedown', outside, true);
      if (detach) { detach(); detach = null; }
      if (scrim) { scrim.remove(); scrim = null; }
      if (menu.parentNode !== sel) sel.appendChild(menu);
      if (focusBtn) selBtn.focus();
    }
    function outside(e) {
      if (scrim && e.target === scrim) return;   // the dimmed page closes it on click, so the tap does not fall through
      if (!sel.contains(e.target) && !menu.contains(e.target)) close(false);
    }
    function openMenu() {
      var ks = list();
      menu.replaceChildren();
      ks.forEach(function (key) {
        var item = el('button', { type: 'button', class: 'us-item', role: 'menuitemradio', 'aria-checked': key === state.kind ? 'true' : 'false',
          onclick: function () { pick(key); } },
          icon(KINDS[key].icon, 20), el('span', null, el('strong', { text: KINDS[key].label }), el('small', { text: KINDS[key].hint })));
        menu.appendChild(item);
      });
      state.open = true;
      if (window.gsUI.isPhone()) {
        scrim = el('div', { class: 'us-scrim' });
        var host = document.getElementById('streams-app') || document.body;
        host.append(scrim, menu);
        menu.hidden = false;
        detach = window.gsUI.sheet(menu, { onClose: function () { close(true); }, scrim: scrim });
      } else menu.hidden = false;
      selBtn.setAttribute('aria-expanded', 'true');
      document.addEventListener('mousedown', outside, true);
      var on = menu.querySelector('[aria-checked="true"]') || menu.firstChild;
      if (on) on.focus();
    }

    function pick(key) {
      clearTimeout(liveTimer);
      state.kind = key;
      remember(key);
      close(true);
      paint();
      if (input.value.trim()) go(state.kind, input.value);
    }

    selBtn.addEventListener('click', function () { if (state.open) close(true); else openMenu(); });
    menu.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && state.open) { e.stopPropagation(); close(true); return; }
      var items = Array.prototype.slice.call(menu.querySelectorAll('.us-item'));
      var i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
      else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
      else if (e.key === 'Tab') close(false);
    });
    form.addEventListener('keydown', function (e) { if (e.key === 'Escape' && state.open) { e.stopPropagation(); close(true); } });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      clearTimeout(liveTimer);
      go(state.kind, input.value);
    });
    input.addEventListener('input', function () {
      if (!opts.live || state.kind !== 'video') return;
      clearTimeout(liveTimer);
      liveTimer = setTimeout(function () { if (opts.onLive && state.kind === 'video') opts.onLive(input.value.trim()); }, 320);
    });

    var ks0 = list();
    var start = opts.kind && ks0.indexOf(opts.kind) >= 0 ? opts.kind : saved();
    state.kind = ks0.indexOf(start) >= 0 ? start : ks0[0];
    paint();

    var api = {
      el: form,
      input: input,
      kind: function () { return state.kind; },
      setKind: function (k) { if (list().indexOf(k) >= 0) { state.kind = k; paint(); } },
      setValue: function (q) { input.value = q || ''; },
      // One line on a phone: the kind as a small chip inside the box, no Search button (Enter searches).
      setCompact: function (on) { form.classList.toggle('is-compact', !!on); },
      refresh: paint
    };
    instances.push(api);
    return api;
  }

  // Books & comics config arrives after the page loads; repaint every box when it does.
  document.addEventListener('gs:books-config', function () { instances.forEach(function (b) { b.refresh(); }); });
  document.addEventListener('gs:me', function () { instances.forEach(function (b) { b.refresh(); }); });

  window.gsUSearch = { create: create, kinds: kinds, bookPath: bookPath, go: go };
})();

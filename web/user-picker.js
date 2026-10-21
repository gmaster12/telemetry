/* Greystar Telemetry: find a person. A search box that opens a dropdown of
   people grouped by the Plex server they are on, plus a server filter.
   Scales to hundreds of people: nothing is listed until the dropdown opens.

   const picker = gsUserPicker({ idPrefix, onPick(person), icon(name, size) });
   picker.setData({ people: [{key, name, username, email, servers: [], plays_30d, admin, disabled, owner}], servers: ['Atlas', ...] });
   container.appendChild(picker.el); */
(function () {
  'use strict';
  function h(tag, props) {
    const e = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (k === 'value') e.value = v;
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (let i = 2; i < arguments.length; i++) {
      [].concat(arguments[i]).forEach((k) => { if (k != null && k !== false) e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k); });
    }
    return e;
  }
  function avatar(name) {
    const s = String(name || '?');
    const hash = Array.from(s).reduce((a, c) => a + c.charCodeAt(0), 0) % 4;
    return h('span', { class: 'sd-avatar sd-avatar--sm sd-avatar--' + hash, 'aria-hidden': 'true', text: (s.replace(/[^A-Za-z0-9]/g, '')[0] || '?').toUpperCase() });
  }
  const NONE = 'Not on a monitored server';

  window.gsUserPicker = function (opts) {
    const pre = opts.idPrefix || 'up';
    const icon = opts.icon || (() => document.createTextNode(''));
    let people = [];
    let servers = [];
    let server = '';
    let open = false;
    let active = -1;
    let options = [];

    const input = h('input', { id: pre + '-q', type: 'text', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': pre + '-list',
      autocomplete: 'off', spellcheck: 'false', placeholder: 'Search by username or email' });
    const toggle = h('button', { type: 'button', class: 'gu-toggle', 'aria-label': 'Show everyone', tabindex: '-1' }, icon('down', 18));
    const sel = h('select', { class: 'gu-server', id: pre + '-server', 'aria-label': 'Plex server' });
    const list = h('div', { class: 'gu-list', id: pre + '-list', role: 'listbox', 'aria-label': 'People' });
    const panel = h('div', { class: 'gu-panel', hidden: true }, list, h('div', { class: 'gu-foot', id: pre + '-foot', 'aria-live': 'polite' }));
    const field = h('div', { class: 'gu-field' }, h('span', { class: 'gu-icon', 'aria-hidden': 'true' }, icon('search', 18)), input, toggle);
    const root = h('div', { class: 'gu' }, h('div', { class: 'gu-row' }, field, sel), panel);

    function matches(p, q) {
      return !q || [p.username, p.name, p.email].some((v) => String(v || '').toLowerCase().includes(q));
    }
    function groups() {
      const q = input.value.trim().toLowerCase();
      const by = new Map();
      const names = server ? [server] : servers.concat(people.some((p) => !p.servers.length) ? [NONE] : []);
      names.forEach((n) => by.set(n, []));
      people.forEach((p) => {
        if (!matches(p, q)) return;
        const on = p.servers.length ? p.servers : [NONE];
        on.forEach((s) => { if (by.has(s)) by.get(s).push(p); });
      });
      by.forEach((arr) => arr.sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' })));
      return Array.from(by.entries()).filter(([, arr]) => arr.length);
    }
    function renderSelect() {
      const keep = server;
      sel.replaceChildren(h('option', { value: '', text: 'All servers (' + people.length + ')' }),
        ...servers.map((s) => h('option', { value: s, text: s + ' (' + people.filter((p) => p.servers.includes(s)).length + ')' })));
      sel.value = servers.includes(keep) ? keep : '';
      server = sel.value;
    }
    function renderList() {
      list.replaceChildren();
      options = [];
      const g = groups();
      const q = input.value.trim();
      let n = 0;
      const seen = new Set();
      g.forEach(([name, arr]) => {
        const gid = pre + '-g-' + name.replace(/[^A-Za-z0-9_-]/g, '_');
        const grp = h('div', { class: 'gu-group', role: 'group', 'aria-labelledby': gid },
          h('div', { class: 'gu-ghead', id: gid }, h('span', { text: name }), h('small', { text: arr.length + (arr.length === 1 ? ' person' : ' people') })));
        arr.forEach((p) => {
          seen.add(p.key);
          const i = options.length;
          const sub = [p.username && p.username !== p.name ? '@' + p.username : null, p.email].filter(Boolean).join(' · ');
          const opt = h('div', { class: 'gu-opt', role: 'option', id: pre + '-o-' + i, 'aria-selected': 'false',
            onmousedown: (e) => e.preventDefault(), onclick: () => pick(p), onmousemove: () => setActive(i, false) },
            avatar(p.name),
            h('span', { class: 'gu-opt__main' }, h('b', { text: p.name }), h('span', { text: sub || 'No email on file' })),
            h('span', { class: 'gu-opt__side' },
              p.admin ? h('span', { class: 'gp-tag gp-tag--admin', text: 'Admin' }) : null,
              p.disabled && !p.admin ? h('span', { class: 'gp-tag gp-tag--off', text: 'Access off' }) : null,
              h('span', { class: 'gu-opt__num', text: (p.plays_30d || 0) + (p.plays_30d === 1 ? ' play' : ' plays') + ', 30 d' })));
          options.push({ el: opt, person: p });
          grp.appendChild(opt);
        });
        list.appendChild(grp);
        n += arr.length;
      });
      const foot = panel.querySelector('.gu-foot');
      if (!g.length) {
        list.appendChild(h('div', { class: 'gu-empty' }, h('b', { text: q ? 'No one matches “' + q + '”' : 'No one here yet' }),
          h('span', { text: q ? 'Search matches part of a username, display name or email address.' : 'Sync Plex users in Settings, Access to load the people you share with.' })));
        foot.textContent = '';
      } else {
        const real = g.filter(([name]) => name !== NONE).length;
        foot.textContent = seen.size + (seen.size === 1 ? ' person' : ' people') + (server ? ' on ' + server : real > 1 ? ' across ' + real + ' servers' : '') +
          (n > seen.size ? ' · people on several servers appear under each' : '');
      }
      active = -1;
      input.removeAttribute('aria-activedescendant');
    }
    function setActive(i, scroll) {
      if (active >= 0 && options[active]) { options[active].el.classList.remove('is-active'); options[active].el.setAttribute('aria-selected', 'false'); }
      active = i;
      if (i < 0 || !options[i]) { input.removeAttribute('aria-activedescendant'); return; }
      const o = options[i].el;
      o.classList.add('is-active'); o.setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', o.id);
      if (scroll !== false) o.scrollIntoView({ block: 'nearest' });
    }
    function show(v) {
      open = v;
      panel.hidden = !v;
      input.setAttribute('aria-expanded', v ? 'true' : 'false');
      toggle.setAttribute('aria-label', v ? 'Hide the list' : 'Show everyone');
      root.classList.toggle('is-open', v);
      if (v) {
        renderList();
        // Near the bottom of the page the list would open off screen: bring it up.
        requestAnimationFrame(() => {
          const r = panel.getBoundingClientRect();
          if (r.bottom > window.innerHeight - 8) {
            const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            root.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
          }
        });
      }
    }
    function pick(p) {
      show(false);
      if (opts.onPick) opts.onPick(p);
    }
    input.addEventListener('focus', () => show(true));
    input.addEventListener('click', () => { if (!open) show(true); });
    input.addEventListener('input', () => { if (!open) show(true); else renderList(); if (input.value.trim()) setActive(0); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (!open) show(true); setActive(Math.min(options.length - 1, active + 1)); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(0, active - 1)); }
      else if (e.key === 'Home' && open && e.ctrlKey) { e.preventDefault(); setActive(0); }
      else if (e.key === 'Enter') { if (open && options[active]) { e.preventDefault(); pick(options[active].person); } }
      else if (e.key === 'Escape') { if (open) { e.preventDefault(); show(false); } else if (input.value) input.value = ''; }
      else if (e.key === 'Tab') show(false);
    });
    toggle.addEventListener('mousedown', (e) => e.preventDefault());
    toggle.addEventListener('click', () => { if (open) show(false); else { input.focus(); show(true); } });
    sel.addEventListener('change', () => { server = sel.value; input.focus(); show(true); });
    document.addEventListener('mousedown', (e) => { if (open && !root.contains(e.target)) show(false); });

    return {
      el: root,
      focus() { input.focus(); show(true); },
      setData(d) {
        people = d.people || [];
        servers = (d.servers || []).slice();
        people.forEach((p) => p.servers.forEach((s) => { if (!servers.includes(s)) servers.push(s); }));
        renderSelect();
        if (open) renderList();
      },
      count() { return people.length; },
      serverCount() { return servers.length; }
    };
  };
})();

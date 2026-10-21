/* Greystar Telemetry sign-in: Plex PIN flow for viewers and the Plex admin,
   username and password for local admin logins. */
(function () {
  'use strict';
  var HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' };
  var plexBtn = document.getElementById('plex');
  var wait = document.getElementById('wait');
  var err = document.getElementById('err');
  var form = document.getElementById('local');
  var showLocal = document.getElementById('show-local');
  var timer = null;

  function post(path, body) {
    return fetch(path, { method: 'POST', headers: HEADERS, credentials: 'same-origin', body: body ? JSON.stringify(body) : undefined })
      .then(read);
  }
  function read(r) {
    return r.json().catch(function () { return null; }).then(function (j) {
      if (!r.ok) {
        var d = j && j.detail;
        if (Array.isArray(d)) d = d.map(function (x) { return x.msg; }).join('; ');
        throw new Error(d || ('Something went wrong (HTTP ' + r.status + ').'));
      }
      return j;
    });
  }
  function showError(text) {
    err.textContent = text;
    err.hidden = !text;
  }
  function showWait(nodes) {
    wait.replaceChildren();
    if (!nodes) { wait.hidden = true; return; }
    var dots = document.createElement('span');
    dots.className = 'lg__dots';
    dots.setAttribute('aria-hidden', 'true');
    dots.innerHTML = '<i></i><i></i><i></i>';
    wait.appendChild(dots);
    nodes.forEach(function (n) { wait.appendChild(typeof n === 'string' ? document.createTextNode(n) : n); });
    wait.hidden = false;
  }
  function stop() {
    if (timer) clearTimeout(timer);
    timer = null;
    plexBtn.disabled = false;
    plexBtn.textContent = 'Sign in with Plex';
  }
  function done() {
    showWait(['Signed in. Opening the dashboard…']);
    window.location.replace('/');
  }

  plexBtn.addEventListener('click', function () {
    showError('');
    // Open the window during the click so pop-up blockers allow it.
    var win = window.open('about:blank', 'plex-auth', 'width=520,height=720');
    plexBtn.disabled = true;
    plexBtn.textContent = 'Waiting for Plex…';
    post('/api/auth/plex/start').then(function (pin) {
      if (win) win.location = pin.auth_url;
      var link = document.createElement('a');
      link.href = pin.auth_url; link.target = '_blank'; link.rel = 'noopener'; link.className = 'lg__link';
      link.textContent = 'open the Plex sign-in page';
      showWait(['Approve the sign-in in the Plex window. If it did not open, ', link, '.']);
      var started = Date.now();
      var poll = function () {
        fetch('/api/auth/plex/check/' + encodeURIComponent(pin.pin_id), { credentials: 'same-origin', cache: 'no-store' })
          .then(read)
          .then(function (r) {
            if (r && r.ok) {
              try { if (win) win.close(); } catch (e) { /* ignore */ }
              done();
              return;
            }
            if (Date.now() - started > 10 * 60 * 1000) throw new Error('The Plex sign-in timed out. Try again.');
            timer = setTimeout(poll, 2000);
          })
          .catch(function (e) {
            try { if (win) win.close(); } catch (x) { /* ignore */ }
            stop(); showWait(null); showError(e.message);
          });
      };
      timer = setTimeout(poll, 2000);
    }).catch(function (e) {
      try { if (win) win.close(); } catch (x) { /* ignore */ }
      stop(); showWait(null); showError(e.message);
    });
  });

  showLocal.addEventListener('click', function () {
    form.hidden = !form.hidden;
    showLocal.setAttribute('aria-expanded', form.hidden ? 'false' : 'true');
    if (!form.hidden) document.getElementById('u').focus();
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    showError('');
    var go = document.getElementById('local-go');
    go.disabled = true; go.textContent = 'Signing in…';
    post('/api/auth/local', { username: document.getElementById('u').value.trim(), password: document.getElementById('p').value })
      .then(done)
      .catch(function (x) { showError(x.message); document.getElementById('p').value = ''; document.getElementById('p').focus(); })
      .finally(function () { go.disabled = false; go.textContent = 'Sign in'; });
  });
})();

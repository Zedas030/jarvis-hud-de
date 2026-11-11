/* iPad home: the same Jarvis (app.js does all the talking, listening and fetching) in a
   room display of its own — clock and day on the left, an orb and his words on the right,
   the house as tiles below. The working parts of the PC page (mic button, stop, ask field,
   timer chip, bell, station and restart) are moved in, so every handler stays as it is.
   The server remembers the last-selected dashboard per device (the PC, each paired iPad or phone),
   so the iPad station keeps its view whatever the PC tries out. Older local preferences are only a
   fallback; a fresh setup opens Sonnenlauf. The layout menu (next to the name in the classic view,
   in ⋯ here) picks from the shared catalogue. A pick the server did not take (an old token after a
   restart, the server away) is kept as "pending" and sent again from the reloaded page, so it is
   never silently lost — but only for ten minutes: an old pending pick must not pin a device to a
   view for good. ?view=<layout-id> previews an entry without changing the saved choice; a page
   opened from the home screen (standalone) ignores it, its launch URL is not a deliberate preview.
   What the server says wins over anything kept locally; without it the page opens Sonnenlauf. */
(function () {
  var q = function (s) { return document.querySelector(s); };
  var params = new URLSearchParams(location.search);
  var stored = function () { try { return localStorage.getItem('jarvis.tabletView'); } catch (e) { return null; } };
  var store = function (v) { try { localStorage.setItem('jarvis.tabletView', v); } catch (e) { /* private mode */ } };
  var ipad = /iPad/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var standalone = navigator.standalone === true || (window.matchMedia && matchMedia('(display-mode: standalone)').matches);
  var catalogue = window.JarvisLayouts;
  var LAYOUTS = catalogue ? catalogue.entries.map(function (entry) { return entry.id; }) : ['tablet', 'stark', 'classic'];
  var DEFAULT = LAYOUTS.indexOf('solstice') >= 0 ? 'solstice' : ipad ? 'tablet' : 'classic';
  var requested = standalone ? null : params.get('view');
  var sharedMeta = document.querySelector('meta[name="jarvis-layout"]');
  var shared = sharedMeta ? sharedMeta.content : '';
  var PENDING = 'jarvis.layoutPending', PENDING_MS = 10 * 60 * 1000;
  var pending = (function () {
    try {
      var raw = JSON.parse(localStorage.getItem(PENDING) || 'null');
      if (typeof raw === 'string') return null;                       // the old format: no age, not trusted
      return raw && Date.now() - raw.at < PENDING_MS ? raw.id : null;
    } catch (e) { return null; }
  })();
  if (LAYOUTS.indexOf(pending) < 0 || pending === shared) pending = null;
  var saveLayout = function (id) {
    var token = document.querySelector('meta[name="jarvis-token"]');
    if (!token) return Promise.reject(new Error('no token'));
    return fetch('/api/layout', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'X-Jarvis-Token': token.content },
      body: JSON.stringify({ layout: id })
    }).then(function (response) { if (!response.ok) throw new Error('layout sync failed: ' + response.status); });
  };
  var clearPending = function () { try { localStorage.removeItem(PENDING); } catch (e) { /* private mode */ } };
  if (!pending) clearPending();
  var choice = LAYOUTS.indexOf(requested) >= 0 ? requested : pending || (LAYOUTS.indexOf(shared) >= 0 ? shared : stored());
  var layout = LAYOUTS.indexOf(choice) >= 0 ? choice : DEFAULT;
  var config = catalogue && catalogue.get(layout);
  if (catalogue) catalogue.activate(layout);
  // Both menus share the catalogue; keep unrelated URL parameters when changing views.
  var pick = function (sel) {
    if (!sel) return;
    sel.value = layout;
    sel.onchange = function () {
      var selected = sel.value;
      store(selected);
      var target = new URL(location.href);
      target.searchParams.delete('view');
      var go = function () { location.href = target.pathname + target.search + target.hash; };
      saveLayout(selected).then(function () { clearPending(); go(); }, function (e) {
        console.warn('[jarvis] layout not saved yet, retrying after reload:', e);
        try { localStorage.setItem(PENDING, JSON.stringify({ id: selected, at: Date.now() })); } catch (err) { /* private mode */ }
        go();
      });
    };
  };
  // the reloaded page has a fresh token: hand the kept pick to the server now
  if (pending && layout === pending) {
    saveLayout(pending).then(clearPending, function (e) { console.warn('[jarvis] layout still not saved:', e); });
  }
  if (layout === 'classic') {
    window.Tablet = { on: false, render: function () {} };
    pick(q('#layoutSel'));
    return;
  }
  var stark = layout === 'stark' && window.Stark;
  var instruments = window.Stark && (stark || (config && config.instruments));
  document.body.classList.add('tablet');
  if (stark) document.body.classList.add('stark');

  // ── move the working parts in ───────────────────────────
  var bar = q('.askbar');
  q('#tDock').appendChild(bar);
  var keys = document.createElement('button');
  keys.className = 'tkeys';
  keys.title = 'Tippen statt sprechen';
  keys.textContent = '⌨';
  bar.insertBefore(keys, q('#btnStop'));
  keys.onclick = function () {
    var typing = bar.classList.toggle('typing');
    if (typing) q('#ask').focus(); else q('#ask').blur();
  };
  q('#ask').addEventListener('keydown', function (e) { if (e.key === 'Enter') bar.classList.remove('typing'); });
  q('#btnSend').addEventListener('click', function () { bar.classList.remove('typing'); });
  q('#tSlots').appendChild(q('#chipTimer'));
  q('#tSlots').appendChild(q('#btnBell'));
  q('#tSheetBtns').appendChild(q('#btnStation'));
  q('#tSheetBtns').appendChild(q('#btnOutput'));
  q('#tSheetBtns').appendChild(q('#btnRestart'));
  document.body.appendChild(q('#nightShade'));     // fixed, but its old parent is hidden now

  var sheet = q('#tSheet');
  q('#tMenuBtn').onclick = function () { sheet.hidden = false; };
  q('#tSheetClose').onclick = function () { sheet.hidden = true; };
  sheet.addEventListener('click', function (e) { if (e.target === sheet) sheet.hidden = true; });
  pick(q('#tLayout'));

  // a few things one says often, one tap each (sent as if typed)
  [['Was steht heute an?', 'Was steht heute an?'], ['Licht an', 'Licht an'], ['Licht aus', 'Licht aus'],
   ['Wetter morgen', 'Wie wird das Wetter morgen?']].forEach(function (a) {
    var b = document.createElement('button');
    b.textContent = a[0];
    b.onclick = function () { if (typeof transmit === 'function') transmit(a[1], { typed: true }); };
    q('#tQuick').appendChild(b);
  });

  // ── clock and greeting ──────────────────────────────────
  function tick() {
    var now = new Date(), h = now.getHours();
    q('#tTime').textContent = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    q('#tDate').textContent = now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
    q('#tHello').textContent = (h < 5 ? 'Gute Nacht' : h < 11 ? 'Guten Morgen' : h < 17 ? 'Guten Tag'
      : h < 22 ? 'Guten Abend' : 'Gute Nacht') + ', Sir.';
  }
  tick();
  setInterval(tick, 5000);
  if (instruments) { Stark.tick(); setInterval(Stark.tick, 1000); }

  // ── his words, big: mirrored from the PC's transcript ──
  var cap = q('#tCap'), me = q('#tMe'), jar = q('#tJar'), lastWords = 0;
  function mirror() {
    var t = q('#transcript');
    var m = t.querySelectorAll('.bubble.me'), j = t.querySelectorAll('.bubble.jarvis:not(.push)');
    var lm = m[m.length - 1], lj = j[j.length - 1];
    // his answer belongs under the question only when it came after it
    var after = lj && lm && (lm.compareDocumentPosition(lj) & Node.DOCUMENT_POSITION_FOLLOWING);
    me.textContent = lm ? lm.textContent : '';
    jar.textContent = lj && (after || !lm) ? lj.textContent : '';
    jar.classList.toggle('thinking', !!(lj && after && lj.classList.contains('thinking')));
    cap.classList.remove('stale');
    lastWords = Date.now();
    cap.scrollTop = cap.scrollHeight;
  }
  new MutationObserver(mirror).observe(q('#transcript'), { childList: true, subtree: true, characterData: true,
                                                          attributes: true, attributeFilter: ['class'] });
  // A confirmation card lands in the hidden transcript; here it must be seen and tapped.
  // Moved as it is, its buttons and countdown keep working (app.js holds the element).
  new MutationObserver(function (list) {
    list.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.classList && n.classList.contains('confirm')) {
          cap.appendChild(n);
          cap.classList.remove('stale');
          cap.scrollTop = cap.scrollHeight;
          syncState();
        }
      });
    });
  }).observe(q('#transcript'), { childList: true });
  // after a while the words step back, the room display stays calm
  setInterval(function () { if (lastWords && Date.now() - lastWords > 90000) cap.classList.add('stale'); }, 5000);

  // ── what the mic is doing, in words and colour ──────────
  var stateEl = q('#tState'), dot = q('#tDot'), mic = q('#btnMic');
  var MODE = { off: 'idle', sleeping: 'sleep', listening: 'listen', hearing: 'hear', busy: 'think',
               speaking: 'speak', 'needs-click': 'alert', blocked: 'alert' };
  function syncState() {
    var st = mic.dataset.state || 'off';
    // a stray "ja" from the room must not approve anything — so say which word counts
    var asking = typeof pendingConfirm !== 'undefined' && pendingConfirm;
    stateEl.textContent = asking ? 'Freigeben? Sag „bestätigt“ oder „nein“ — oder tippe unten' : q('#micLabel').textContent;
    stateEl.className = 'tstate ' + (asking ? 'hearing' : st);
    Orb.mode = MODE[st] || 'idle';
    var c = Orb.colour();
    dot.style.background = c; dot.style.boxShadow = '0 0 12px ' + c;
  }

  // ── the orb (replaces the PC's dial; app.js calls JarvisOrb.setState / setLevel) ──
  var Orb = {
    cv: q('#tOrb'), mode: 'idle', level: 0, shown: 0, t0: performance.now(), last: 0,
    COLOURS: { idle: '#5d7890', sleep: '#3fb6d8', listen: '#5fe4ff', hear: '#ffb340', think: '#b98cff',
               speak: '#5fe4ff', alert: '#ff6ba6' },
    colour: function () { return Orb.COLOURS[Orb.mode] || Orb.COLOURS.idle; },
    frame: function (now) {
      requestAnimationFrame(Orb.frame);
      var night = document.body.classList.contains('night') && !document.body.classList.contains('night-active');
      // ~30 fps, slow breathing at night or when nobody talks: the iPad runs all day
      var gap = night ? 200 : (Orb.mode === 'speak' || Orb.mode === 'hear' || Orb.mode === 'think') ? 33 : 66;
      if (now - Orb.last < gap || Orb.cv.offsetParent === null) return;
      Orb.last = now;
      var cv = Orb.cv, dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
        cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      }
      var ctx = cv.getContext('2d'), t = (now - Orb.t0) / 1000, c = Orb.colour();
      if (stark) {
        Orb.shown += (Math.min(1, Orb.level) - Orb.shown) * 0.3;
        Orb.level *= 0.88;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        return Stark.reactor(ctx, W, H, t, Stark.colour(Orb.mode), Orb.mode, Orb.shown);
      }
      Orb.shown += (Math.min(1, Orb.level) - Orb.shown) * 0.25;
      Orb.level *= 0.9;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      var cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.2;   // glow (2.1 R) and voice stay inside the canvas
      var breathe = 1 + Math.sin(t * (Orb.mode === 'sleep' || Orb.mode === 'idle' ? 0.9 : 2.2)) * 0.035;
      var r = R * breathe * (1 + Orb.shown * 0.22);
      // glow
      var g = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 2.1);
      g.addColorStop(0, hexA(c, 0.42)); g.addColorStop(0.45, hexA(c, 0.12)); g.addColorStop(1, hexA(c, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r * 2.1, 0, Math.PI * 2); ctx.fill();
      // core
      var k = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
      k.addColorStop(0, hexA('#ffffff', 0.55)); k.addColorStop(0.35, hexA(c, 0.55)); k.addColorStop(1, hexA(c, 0.08));
      ctx.fillStyle = k; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
      // rings: a soft wobble that follows the voice
      ctx.lineWidth = 1.4;
      for (var i = 0; i < 3; i++) {
        ctx.strokeStyle = hexA(c, 0.5 - i * 0.13);
        ctx.beginPath();
        for (var a = 0; a <= 64; a++) {
          var ang = a / 64 * Math.PI * 2;
          var wob = Math.sin(ang * (3 + i) + t * (1.2 + i * 0.5)) * (2 + Orb.shown * 14) * (i + 1) * 0.6;
          var rr = r * (1.18 + i * 0.16) + wob;
          var x = cx + Math.cos(ang) * rr, y = cy + Math.sin(ang) * rr;
          if (a) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        ctx.stroke();
      }
      // thinking: an arc that runs around
      if (Orb.mode === 'think') {
        ctx.strokeStyle = hexA(c, 0.9); ctx.lineWidth = 3; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(cx, cy, r * 1.62, t * 2.4, t * 2.4 + Math.PI * 0.55); ctx.stroke();
      }
    }
  };
  if (config && config.accent) {
    ['idle', 'sleep', 'listen', 'speak'].forEach(function (mode) { Orb.COLOURS[mode] = config.accent; });
  }
  function hexA(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  window.JarvisOrb = {
    setState: function () { syncState(); },
    setLevel: function (v) { Orb.level = Math.max(Orb.level, Math.max(0, Math.min(1.4, v || 0))); }
  };
  new MutationObserver(syncState).observe(mic, { attributes: true, attributeFilter: ['data-state'] });
  new MutationObserver(syncState).observe(q('#micLabel'), { childList: true, characterData: true, subtree: true });
  requestAnimationFrame(Orb.frame);
  syncState();
  setInterval(syncState, 1000);   // a card answered or timed out changes no mic state

  // ── the day and the house, from /api/dashboard (app.js calls render) ──
  var SKY = function (code) {
    if (code === 0) return '☀️';
    if (code <= 2) return '🌤️';
    if (code === 3) return '☁️';
    if (code === 45 || code === 48) return '🌫️';
    if (code >= 95) return '⛈️';
    if ((code >= 71 && code <= 77) || code === 85 || code === 86) return '🌨️';
    if (code >= 80) return '🌦️';
    if (code >= 51) return '🌧️';
    return '🌥️';
  };
  var num = function (v, d) { return v == null || isNaN(v) ? '—' : Number(v).toFixed(d || 0).replace('.', ','); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };

  /* One line per day, the same for today and the next days, 0 to 24 h:
     "Regen 6–12 Uhr (30–100 %), 23–24 Uhr (18 %) · ~1,8 mm" — each stretch with its own chance */
  function rainText(d) {
    if (!d) return '';
    var mm = d.mm >= 0.1 ? ' · ~' + num(d.mm, d.mm < 10 ? 1 : 0) + ' mm' : '';
    return d.rain_when ? 'Regen ' + d.rain_when + mm : 'kein Regen' + (d.rain ? ' (höchstens ' + d.rain + ' %)' : '');
  }

  function weather(w) {
    if (!w || w.error) return;
    q('#tSky').textContent = SKY(w.code);
    q('#tTemp').textContent = num(w.now) + '°';
    q('#tSkyText').textContent = w.sky;
    q('#tRange').textContent = num(w.tmin) + '° – ' + num(w.tmax) + '° · ' + rainText((w.days || [])[0]) + ' · ' + w.golf
      + ' (Golf ' + (w.golf_day || 'heute') + ')';
    q('#tDays').innerHTML = (w.days || []).map(function (d, i) {
      return '<li data-day="' + i + '"><b>' + esc(d.day) + '</b><span>' + SKY(d.code) + '</span><em>' + num(d.tmax) + '°<small>'
        + num(d.tmin) + '°</small></em><i>' + (d.rain_times ? 'bis ' + d.rain + ' %' : '') + '</i>'
        + '<i class="twhen">' + esc(d.rain_times || '') + '</i></li>';
    }).join('');
  }

  function agenda(a) {
    var ol = q('#tAgenda');
    if (a == null) { ol.innerHTML = '<li class="tempty">Kalender nicht verbunden</li>'; return; }
    if (!a.length) { ol.innerHTML = '<li class="tempty">Die Woche ist frei.</li>'; return; }
    ol.innerHTML = a.map(function (e) {
      return '<li class="' + (e.now ? 'now' : '') + '"><b>' + esc(e.day) + (e.time ? ' · ' + esc(e.time) : '') + '</b><span>'
        + esc(e.title) + '</span></li>';
    }).join('');
  }

  function tile(cls, label, value, unit, sub, bar, kind) {
    return '<div class="ttile ' + (cls || '') + '" data-kind="' + (kind || '') + '" role="button"><span>' + label + '</span><b>' + value
      + (unit ? '<small>' + unit + '</small>' : '') + '</b>' + (sub ? '<em>' + sub + '</em>' : '')
      + (bar != null ? '<i><u style="width:' + Math.max(0, Math.min(100, bar)) + '%"></u></i>' : '') + '</div>';
  }

  function tiles(d) {
    var h = d.house || {}, m = d.mail || {}, me = d.me || {}, out = [];
    if (h.where) {     // where the presence sensors see me: answers and "Licht an" go there
      var w = h.where;
      out.push(tile(w.offline ? 'warn' : '', 'Raum', esc(w.offline ? 'unbekannt' : (w.room || 'niemand')), '',
        w.offline ? 'Home Assistant nicht erreichbar' : !w.room ? 'kein Melder sieht jemanden'
          : w.sure ? 'Präsenzmelder' : 'unsicher · ' + esc((w.candidates || []).join(' oder ')), null, null));
    }
    if (!h.error && h.solar_w != null) {
      out.push(tile(h.solar_w > 50 ? 'good' : '', 'Solar', num(h.solar_w), 'W', 'heute ' + num(h.gen_kwh, 1) + ' kWh', null, 'energy'));
      out.push(tile(h.soc != null && h.soc < 20 ? 'warn' : '', 'Speicher', num(h.soc), '%',
        h.charge_w > 5 ? 'lädt ' + num(h.charge_w) + ' W' : h.out_w > 5 ? 'gibt ' + num(h.out_w) + ' W' : 'ruht', h.soc, 'energy'));
    }
    if (h.rooms && h.rooms.length) {
      out.push('<div class="ttile rooms" data-kind="rooms" role="button"><span>Räume</span><ul>' + h.rooms.slice(0, 3).map(function (r) {
        return '<li class="' + (r.heating ? 'heat' : '') + '">' + esc(r.name) + '<b>' + num(r.temp, 1) + '°</b></li>';
      }).join('') + '</ul></div>');
    }
    if (h.pm25 != null) out.push(tile(h.air === 'gut' ? 'good' : h.air === 'schlecht' ? 'bad' : 'warn', 'Luft', esc(h.air || '—'), '',
      'PM2,5 · ' + num(h.pm25), null, 'air'));
    if (!m.error && m.unread != null) {
      var first = (m.latest || [])[0];
      out.push(tile(m.unread ? '' : 'good', 'Post', num(m.unread), 'neu', first ? esc(first.sender) : 'alles gelesen', null, 'mail'));
    }
    if (!me.error && (me.sleep_h != null || me.battery != null)) {
      out.push(tile(me.battery != null && me.battery < 30 ? 'warn' : '', 'Schlaf', num(me.sleep_h, 1), 'h',
        'Energie ' + num(me.battery) + (me.sleep_score != null ? ' · Score ' + me.sleep_score : ''), me.battery, 'sleep'));
    }
    var tech = techState(d);
    if (tech.total) out.push(tile(tech.bad ? 'warn' : 'good', 'Technik', tech.bad ? tech.bad : '✓', tech.bad ? 'Probleme' : '',
      tech.total + ' Geräte geprüft', null, 'tech'));
    var u = d.usage || {};
    if (u.total) {
      var trend = u.trend_pct == null ? '' : ' · ' + (u.trend_pct > 0 ? '+' : '') + u.trend_pct + ' %';
      var budget = u.budget_pct == null ? '' : ' · Budget ' + u.budget_pct + ' %';
      out.push(tile((u.budget_pct != null && u.budget_pct >= 80) || (u.trend_pct != null && u.trend_pct > 25) ? 'warn' : '',
        'Tokens', num(Math.round(u.tokens / 1000)), 'k',
        (u.local_pct != null ? u.local_pct + ' % lokal' : '') + trend + budget, u.local_pct, 'usage'));
    }
    q('#tTiles').innerHTML = out.join('');
  }

  // NAS, Pi and router: one tile that says whether all is well, the sheet says what
  function techState(d) {
    var h = d.house || {}, n = d.nas || {}, p = d.pi || {}, r = h.router, total = 0, bad = 0;
    if (n.model || n.error) { total++; if (n.error || n.ok === false) bad++; }
    if (p.temp != null || p.error) { total++; if (p.error || (p.containers && p.up < p.containers)) bad++; }
    if (r && !h.error) { total++; if (!r.online) bad++; }
    return { total: total, bad: bad };
  }

  // ── more on a tap: every tile and the weather open a sheet with the details ──
  var last = null, sheetKind = '', sheetDay = null;
  var detail = q('#tDetail'), dbody = q('#tDetailBody'), dtitle = q('#tDetailTitle');
  var row = function (k, v, cls) { return '<li class="' + (cls || '') + '"><span>' + k + '</span><b>' + v + '</b></li>'; };
  var list = function (rows) { return '<ul class="tdlist">' + rows.join('') + '</ul>'; };
  var note = function (t) { return '<p class="tdnote">' + esc(t) + '</p>'; };
  var DETAIL = {
    weather: function (d) {
      var w = d.weather || {};
      if (w.error) return ['Wetter', note('Wetter gerade nicht abrufbar.')];
      var hour = new Date().getHours();
      // every day the same: a line, then its 24 hours as bars (today's past hours dimmed)
      var dayBlock = function (x, k) {
        var hours = x.hours || [], max = Math.max.apply(null, hours.map(function (y) { return y.mm; }).concat([1]));
        return '<div class="tdday" data-day="' + k + '"><div class="tdhead"><b>' + esc(x.day) + '</b><span>' + SKY(x.code) + '</span><em>'
          + num(x.tmin) + '° – ' + num(x.tmax) + '°</em></div><p class="tdline">' + esc(rainText(x)) + '</p>'
          + (hours.length ? '<div class="tdrain">' + hours.map(function (y) {
            return '<div class="' + (y.rain ? 'likely' : '') + (k === 0 && y.h < hour ? ' past' : '') + '"><b>'
              + (y.rain ? y.p : '') + '</b><i style="height:' + Math.max(2, y.p) + '%"></i><u style="height:'
              + Math.round(y.mm / max * 100) + '%"></u><em>' + (y.h % 3 === 0 ? y.h : '') + '</em></div>';
          }).join('') + '</div>' : '') + '</div>';
      };
      return ['Wetter', '<p class="tdlead">' + SKY(w.code) + ' ' + num(w.now) + '° · ' + esc(w.sky) + '</p>'
        + list([row('Wind bis', num(w.wind) + ' km/h'), row('Sonnenuntergang', esc(w.sunset)),
                row('Golf ' + esc(w.golf_day || 'heute'), esc(w.golf))])
        + '<p class="tdnote"><i class="k1"></i> Regenwahrscheinlichkeit je Stunde · <i class="k2"></i> Menge · Zahl = Regenstunde (ab 30 %, mit Menge ab 10 %)</p>'
        + (w.days || []).map(dayBlock).join('')];
    },
    energy: function (d) {
      var h = d.house || {};
      var rows = [row('Solar jetzt', num(h.solar_w) + ' W', h.solar_w > 50 ? 'good' : ''),
                  row('Erzeugt heute', num(h.gen_kwh, 1) + ' kWh'),
                  row('Speicher', num(h.soc) + ' %', h.soc != null && h.soc < 20 ? 'warn' : ''),
                  row('Lädt mit', h.charge_w > 5 ? num(h.charge_w) + ' W' : '—'),
                  row('Gibt ab', h.out_w > 5 ? num(h.out_w) + ' W' : '—')];
      (h.bats || []).forEach(function (b, i) { rows.push(row('Batterie ' + (i + 1), num(b) + ' %', b < 20 ? 'warn' : '')); });
      return ['Solar und Speicher', list(rows)];
    },
    rooms: function (d) {
      var rs = (d.house || {}).rooms || [];
      return ['Räume', list(rs.map(function (r) {
        // a target of 5-7 degrees is the thermostat's frost guard: heating off, as far as a person cares
        var set = r.target == null ? '' : r.target <= 7 ? ' <small>Heizung aus</small>' : ' <small>Soll ' + num(r.target, 1) + '°</small>';
        return row(esc(r.name), num(r.temp, 1) + '°' + set
          + (r.heating ? ' <small>heizt</small>' : ''), r.heating ? 'warn' : '');
      }))];
    },
    air: function (d) {
      var h = d.house || {};
      return ['Luft', list([row('Bewertung', esc(h.air || '—'), h.air === 'gut' ? 'good' : h.air === 'schlecht' ? 'bad' : 'warn'),
                            row('Feinstaub PM2,5', num(h.pm25, 1) + ' µg/m³')])
        + note('Bis 12 µg/m³ gut, bis 35 mäßig, darüber schlecht.')];
    },
    mail: function (d) {
      var m = d.mail || {};
      if (m.error) return ['Post', note(m.error)];
      return ['Post', list([row('Ungelesen', num(m.unread)), row('Postfächer', num(m.accounts))])
        + '<h4>Ungelesen' + (m.inbox && m.unread > m.inbox.length ? ' · neueste ' + m.inbox.length : '') + '</h4>'
        + '<ul class="tdmail">' + (m.inbox || m.latest || []).map(function (x) {
          return '<li><b>' + esc(x.sender) + '</b><span>' + esc(x.subject) + '</span><em>' + esc(x.when) + ' · ' + esc(x.account) + '</em></li>';
        }).join('') + '</ul>'
        + ((m.errors || []).length ? note('Nicht erreichbar: ' + m.errors.join('; ')) : '')
        + note('Stand ' + (m.at || '') + ' · nichts wird als gelesen markiert.')];
    },
    sleep: function (d) {
      var me = d.me || {};
      if (me.error) return ['Schlaf', note(me.error)];
      return ['Schlaf und Energie', list([row('Schlaf', num(me.sleep_h, 1) + ' h'), row('Schlaf-Score', num(me.sleep_score)),
        row('Body Battery', num(me.battery), me.battery != null && me.battery < 30 ? 'warn' : ''),
        row('Schritte', me.steps != null ? Number(me.steps).toLocaleString('de-DE') : '—'),
        row('Ruhepuls', me.rhr != null ? me.rhr + ' bpm' : '—'), row('Stress', num(me.stress))])
        + note('Garmin, Stand ' + (me.at || ''))];
    },
    tech: function (d) {
      var h = d.house || {}, n = d.nas || {}, p = d.pi || {}, r = h.router || {}, out = '';
      if (n.model || n.error) out += '<h4>NAS</h4>' + (n.error ? note(n.error) : list([
        row('Zustand', n.ok ? 'in Ordnung' : 'prüfen', n.ok ? 'good' : 'bad'), row('Modell', esc(n.model)),
        row('Belegt', num(n.used_tb, 1) + ' / ' + num(n.total_tb, 1) + ' TB (' + num(n.used_pct) + ' %)', n.used_pct > 85 ? 'warn' : ''),
        row('Temperatur', num(n.temp) + ' °C'), row('Festplatten', num(n.disks))]));
      if (p.temp != null || p.error) out += '<h4>Raspberry Pi</h4>' + (p.error ? note(p.error) : list([
        row('Container', p.up + ' / ' + p.containers + ' laufen', p.up < p.containers ? 'bad' : 'good'),
        row('Temperatur', num(p.temp, 1) + ' °C', p.temp > 70 ? 'warn' : ''), row('Last', num(p.load, 2)),
        row('Speicherkarte', num(p.disk) + ' % belegt'), row('Läuft seit', esc(p.uptime || '—'))]));
      if (h.router && !h.error) out += '<h4>Internet</h4>' + list([row('Verbindung', r.online ? 'online' : 'offline', r.online ? 'good' : 'bad'),
        row('Download', num(r.down * 8 / 1000, 1) + ' Mbit/s'), row('Upload', num(r.up * 8 / 1000, 1) + ' Mbit/s')]);   // the router counts kB/s
      return ['Technik', out || note('Keine Geräte verbunden.')];
    },
    usage: function (d) {
      var u = d.usage || {};
      if (!u.total) return ['Tokens', note('Noch keine Anfragen diese Woche.')];
      var rows = [row('Anfragen (7 Tage)', num(u.total)), row('Lokal erledigt', u.local_pct != null ? u.local_pct + ' %' : '—', u.local_pct >= 60 ? 'good' : ''),
                  row('Ans Modell', num(u.llm)), row('Tokens diese Woche', Number(u.tokens || 0).toLocaleString('de-DE')),
                  row('Vorwoche', Number(u.prev_tokens || 0).toLocaleString('de-DE')),
                  row('Wochenbudget seit Montag', u.budget_pct == null ? 'aus' : Number(u.budget_used || 0).toLocaleString('de-DE') + ' von '
                      + Number(u.budget || 0).toLocaleString('de-DE') + ' (' + u.budget_pct + ' %)', u.budget_pct >= 100 ? 'bad' : u.budget_pct >= 80 ? 'warn' : ''),
                  row('Trend', u.trend_pct == null ? '—' : (u.trend_pct > 0 ? '+' : '') + u.trend_pct + ' %', u.trend_pct > 25 ? 'warn' : u.trend_pct < 0 ? 'good' : ''),
                  row('Gelernte Formulierungen', num(u.aliases) + (u.drafts ? ' (+' + u.drafts + ' Entwürfe)' : ''))];
      if (u.misses != null) rows.push(row('Gleich nochmal gefragt', num(u.misses), u.misses > 3 ? 'warn' : ''));
      var top = (u.top || []).map(function (t) {
        return '<li><b>' + esc(t.msg) + '</b><span>' + t.count + '× · ' + Number(t.tokens || 0).toLocaleString('de-DE') + ' Tokens</span></li>';
      }).join('');
      return ['Tokens und Router', list(rows) + (top ? '<h4>Teuerste Wiederholungen</h4><ul class="tdmail">' + top + '</ul>'
        + note('Wiederholte Fragen, die noch ans Modell gehen — Kandidaten für den Router.') : '')];
    }
  };
  /* day: a tapped day in the forecast row — the sheet opens on that day, not on today */
  function openDetail(kind, day) {
    if (!DETAIL[kind] || !last) return;
    sheetKind = kind; sheetDay = day;
    var r = DETAIL[kind](last);
    dtitle.textContent = r[0];
    dbody.innerHTML = r[1];
    detail.hidden = false;
    var target = day != null && dbody.querySelector('.tdday[data-day="' + day + '"]');
    if (target) {
      target.classList.add('focus');
      dbody.scrollTop = target.offsetTop - dbody.offsetTop - 4;
    } else dbody.scrollTop = 0;
  }
  function closeDetail() { detail.hidden = true; sheetKind = ''; }
  q('#tTiles').addEventListener('click', function (e) {
    var t = e.target.closest('.ttile');
    if (t && t.dataset.kind) openDetail(t.dataset.kind);
  });
  q('.tweather').addEventListener('click', function (e) {
    var li = e.target.closest('#tDays li');
    openDetail('weather', li ? li.dataset.day : null);
  });
  q('#tDetailClose').onclick = closeDetail;
  detail.addEventListener('click', function (e) { if (e.target === detail) closeDetail(); });

  window.Tablet = {
    on: true,
    render: function (d) {
      last = d;
      if (instruments) { try { Stark.render(d); } catch (e) { /* the HUD's own panels; the rest stays */ } }
      try { weather(d.weather); } catch (e) { /* one part failing leaves the rest */ }
      try { agenda(d.agenda); } catch (e) { /* idem */ }
      try { tiles(d); } catch (e) { /* idem */ }
      if (window.JarvisLayoutPlus) { try { JarvisLayoutPlus.render(d); } catch (e) { console.warn("[jarvis] layout panels:", e); } }
      // an open sheet follows the new figures, its scroll position stays
      try {
        if (sheetKind) {
          dbody.innerHTML = DETAIL[sheetKind](d)[1];
          var f = sheetDay != null && dbody.querySelector('.tdday[data-day="' + sheetDay + '"]');
          if (f) f.classList.add('focus');
        }
      } catch (e) { /* idem */ }
    }
  };
  // app.js fetched the first round before this file was read: fetch it again, not in 30 s
  if (typeof loadDashboard === 'function') loadDashboard();
})();

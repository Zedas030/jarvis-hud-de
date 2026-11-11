/* JARVIS · Claude Code memory HUD — front end.
   Talks to the local Python server over NDJSON. The brain is your `claude` CLI,
   so every token here is billed to your subscription, not an API key. */
'use strict';

var $ = function (s) { return document.querySelector(s); };
var TOKEN = document.querySelector('meta[name=jarvis-token]').content;
var headers = function (extra) {
  return Object.assign({ 'X-Jarvis-Token': TOKEN }, extra || {});
};
var esc = function (s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
};
var now = function () { return new Date().toTimeString().slice(0, 8); };

var graph = null, hiddenTypes = new Set(), running = false, activeCtl = null, streamSpeak = false;
var answerText = '', answerBubble = null, GRAPH = { nodes: [], links: [], hubs: [], counts: [] };

/* ── action log ───────────────────────────── */
function log(kind, label, msg) {
  var e = document.createElement('div');
  e.className = 'entry k-' + kind;
  e.innerHTML = '<div class="top"><span class="ts">' + now() + '</span>'
    + '<span class="kind">' + esc(label) + '</span></div>'
    + (msg ? '<div class="msg">' + esc(msg) + '</div>' : '');
  var box = $('#log');
  box.insertBefore(e, box.firstChild);
  while (box.children.length > 45) box.removeChild(box.lastChild);
}

/* Fish Audio reads [square brackets] as stage directions, never as words. We
   send the text through untouched so the model performs them, but on screen we
   render each tag as a chip so you can see the direction and hear the result.
   The browser speechSynthesis fallback has no such notion, so it gets a
   stripped copy — otherwise it literally reads "dry" out loud. */
var TONE_RE = /\[([^\][]{1,90})\]/g;

function stripTones(text) {
  return String(text || '').replace(TONE_RE, ' ').replace(/\s{2,}/g, ' ').trim();
}

function lastTone(text) {
  var found = null, m;
  TONE_RE.lastIndex = 0;
  while ((m = TONE_RE.exec(text)) !== null) found = m[1];
  return found;
}

function renderSpoken(el, text) {
  var html = '', last = 0, m;
  TONE_RE.lastIndex = 0;
  while ((m = TONE_RE.exec(text)) !== null) {
    html += esc(text.slice(last, m.index));
    html += '<span class="tone">' + esc(m[1]) + '</span>';
    last = m.index + m[0].length;
  }
  html += esc(text.slice(last));
  el.innerHTML = html;
}

function bubble(who, text) {
  var b = document.createElement('div');
  b.className = 'bubble ' + who;
  if (who === 'jarvis') renderSpoken(b, text); else b.textContent = text;
  document.body.classList.add('hasconvo');
  $('#transcript').appendChild(b);
  var turns = $('#transcript').querySelectorAll('.bubble.me').length;
  $('#convoCount').textContent = turns + (turns === 1 ? ' turn' : ' turns');
  $('#transcript').scrollTop = $('#transcript').scrollHeight;
  return b;
}

function setVoiceState(state, cls) {
  $('#voiceState').textContent = state;
  if (window.JarvisOrb) JarvisOrb.setState(state);
  $('#voiceDot').className = 'dot ' + (cls || '');
  updateMicButton();
}

/* The Live button says in plain words what the mic is doing right now. */
var micProblem = null;   // 'blocked' | 'needs-click' | null
function updateMicButton() {
  var btn = $('#btnMic'), label = $('#micLabel');
  if (!btn || !label || typeof Live === 'undefined') return;
  var st = 'off', text = 'Live starten';
  if (micProblem === 'blocked') { st = 'blocked'; text = 'Mikrofon blockiert'; }
  else if (Live.ptt) { st = 'hearing'; text = 'Loslassen = senden'; }
  else if (!Live.on) { st = 'off'; text = 'Live starten'; }
  else if (micProblem === 'needs-click' || (Live.ctx && Live.ctx.state === 'suspended')) {
    st = 'needs-click'; text = 'Klick = Mikro an';
  }
  // picking the iPad up, music, talk: recorded on the device, but nothing for him until "Jarvis"
  else if (Live.armed && typeof OnDevice !== 'undefined' && OnDevice.gate() && !Wake.awake()) { st = 'sleeping'; text = Ambience.hint(); }
  else if (Live.armed) { st = 'hearing'; text = 'Höre dich …'; }
  else if (Live.sending) { st = 'busy'; text = 'Verstehe …'; }
  else if (Live.speaking) { st = 'speaking'; text = (typeof VoiceClient !== 'undefined' && VoiceClient.on) ? 'Spricht · reinreden unterbricht' : 'Spricht · „Jarvis“ unterbricht'; }
  else if (running) { st = 'busy'; text = briefingActive ? 'Briefing · reden bricht ab' : 'Denkt …'; }
  else if (Ringing.on) { st = 'speaking'; text = 'Klingelt · reden = aus'; }
  else if (!Wake.awake()) { st = 'sleeping'; text = Ambience.hint(); }
  else { st = 'listening'; text = Wake.mode() === 'off' ? 'Hört zu' : 'Ich höre …'; }
  btn.dataset.state = st;
  btn.classList.toggle('live', Live.on && st !== 'blocked');
  document.body.classList.toggle('mic-tap', st === 'needs-click');   // the station shows a full-screen "tap"
  // At night the station lifts its black screen only while a conversation runs.
  document.body.classList.toggle('night-active', st === 'hearing' || st === 'busy' || st === 'speaking' || !!pendingConfirm);
  if (label.textContent !== text) label.textContent = text;
}

/* ── panels ───────────────────────────────── */
/* ── live widgets around the graph ───────────
   Haus, Gesundheit, Systeme and the top bar, fed by /api/dashboard. Each source
   is cached server-side; a missing one just shows a dash. */
var RING_C = 2 * Math.PI * 27;
function gauge(el, frac, colour, value, unit, label) {
  frac = Math.max(0, Math.min(1, frac || 0));
  el.innerHTML = '<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="27" class="gbase"/>'
    + '<circle cx="32" cy="32" r="27" class="gval" style="stroke:' + colour
    + ';stroke-dasharray:' + RING_C.toFixed(1) + ';stroke-dashoffset:' + (RING_C * (1 - frac)).toFixed(1)
    + ';filter:drop-shadow(0 0 4px ' + colour + ')"/></svg>'
    + '<div class="gtext"><b>' + esc(value) + '<small>' + esc(unit) + '</small></b></div>'
    + '<span class="glabel">' + esc(label) + '</span>';
}
function de(n, digits) {
  return n == null ? '—' : Number(n).toLocaleString('de-DE', { maximumFractionDigits: digits || 0 });
}
function tone(v, okAt, warnAt, higherIsBetter) {
  if (v == null) return 'var(--ink-dim)';
  var good = higherIsBetter ? v >= okAt : v <= okAt, mid = higherIsBetter ? v >= warnAt : v <= warnAt;
  return good ? 'var(--green)' : mid ? 'var(--amber)' : 'var(--pink)';
}

function renderHouse(h) {
  if (!h || h.error) { $('#houseLine').textContent = h ? 'Home Assistant: ' + h.error : 'lädt …'; return; }
  gauge($('#gSolar'), h.solar_w / 2000, 'var(--amber)', de(h.solar_w), 'W', 'Solar');
  gauge($('#gSoc'), (h.soc || 0) / 100, tone(h.soc, 50, 20, true), de(h.soc), '%', 'Speicher');
  $('#houseLine').innerHTML = '<span>heute <b>' + de(h.gen_kwh, 1) + ' kWh</b></span>'
    + '<span>Batterien <b>' + h.bats.map(function (b) { return de(b); }).join(' / ') + ' %</b></span>';
  $('#rooms').innerHTML = h.rooms.map(function (r) {
    return '<li><span class="nm">' + esc(r.name) + '</span>'
      + (r.heating ? '<i class="flame" title="heizt auf ' + de(r.target, 1) + ' °C">▲</i>' : '')
      + '<b>' + de(r.temp, 1) + ' °C</b></li>';
  }).join('');
  var pm = h.pm25, col = tone(pm, 12, 35, false);
  $('#airFill').style.width = Math.min(100, (pm || 0) / 75 * 100) + '%';
  $('#airFill').style.background = col;
  $('#airVal').innerHTML = pm == null ? '—' : de(pm) + ' <small>µg</small>';
  $('#airVal').style.color = col;
}

function renderMe(m) {
  if (!m || m.error) {
    $('#meTiles').innerHTML = '<p class="hint">' + esc(m ? 'Garmin: ' + m.error : 'Uhr wird gelesen …') + '</p>';
    return;
  }
  gauge($('#gSleep'), (m.sleep_score || 0) / 100, tone(m.sleep_score, 75, 60, true),
    m.sleep_score == null ? '—' : m.sleep_score, '', 'Schlaf');
  gauge($('#gBattery'), (m.battery || 0) / 100, tone(m.battery, 50, 25, true),
    m.battery == null ? '—' : m.battery, '', 'Body Battery');
  var tile = function (k, v, u) { return '<div><span>' + k + '</span><b>' + v + '<small>' + u + '</small></b></div>'; };
  $('#meTiles').innerHTML = tile('SCHLAF', de(m.sleep_h, 1), ' h') + tile('SCHRITTE', de(m.steps), '')
    + tile('RUHEPULS', de(m.rhr), ' bpm') + tile('STRESS', de(m.stress), '');
  $('#meAge').textContent = 'garmin · ' + m.at;
}

/* Only sender and subject — the bodies stay in the mailbox. */
function renderMail(m) {
  if (!m || m.error) {
    $('#mailUnread').textContent = '—';
    $('#mailList').innerHTML = '<p class="hint">' + esc(m ? 'Postfach: ' + m.error : 'Postfach wird gelesen …') + '</p>';
    return;
  }
  $('#mailUnread').textContent = m.unread;
  $('#mailUnread').className = m.unread ? '' : 'zero';
  var many = (m.accounts || 1) > 1;
  // an account that does not answer (wrong password, IMAP off) says so here instead of vanishing
  var broken = (m.errors || []).map(function (e) {
    return '<li class="warn"><i class="dot"></i><span class="nm">' + esc(e.split(':')[0]) + ' nicht erreichbar</span><em>'
      + esc(e.split(':').slice(1).join(':').trim()) + '</em></li>';
  }).join('');
  $('#mailList').innerHTML = broken + (m.latest.map(function (x) {
    return '<li class="ok"><i class="dot"></i><span class="nm">' + (many ? '<small>' + esc(x.account) + ' · </small>' : '')
      + esc(x.sender) + ' <small>' + esc(x.when) + '</small></span><em>' + esc(x.subject) + '</em></li>';
  }).join('') || (broken ? '' : '<p class="hint">Alles gelesen.</p>'));
  $('#mailAge').textContent = 'Mail · ' + (many ? m.accounts + ' Konten · ' : '') + m.at;
}

function renderSystems(d) {
  var rows = [], h = d.house, p = d.pi, n = d.nas;
  var row = function (state, name, detail) { rows.push([state, name, detail]); };
  if (p) row(p.error ? 'bad' : (p.up < p.containers || p.temp > 70 ? 'warn' : 'ok'), 'Raspberry Pi',
    p.error ? p.error : de(p.temp, 1) + ' °C · ' + p.up + '/' + p.containers + ' Container');
  if (n) row(n.error ? 'bad' : (!n.ok || n.used_pct > 85 ? 'warn' : 'ok'), 'NAS',
    n.error ? n.error : de(n.used_pct) + ' % belegt · ' + n.disks + ' Platten ' + (n.ok ? 'gesund' : 'prüfen'));
  if (h && !h.error) row(h.router.online ? 'ok' : 'bad', 'Router',
    h.router.online ? '↓ ' + de(h.router.down * 8 / 1000, 1) + ' · ↑ ' + de(h.router.up * 8 / 1000, 1) + ' Mbit/s' : 'offline');
  if (h) row(h.error ? 'bad' : 'ok', 'Home Assistant', h.error ? 'nicht erreichbar' : 'verbunden');
  var rs = d.resources;
  if (rs && rs.ok !== null) row(rs.ok ? (rs.shed && rs.shed.length ? 'warn' : 'ok') : 'warn', 'GPU-Budget',
    rs.text + (rs.shed && rs.shed.length ? ' · abgegeben: ' + rs.shed.join(', ') : ''));
  var c = d.pc;
  if (c && !c.error) row((c.pc_gpu_temp > 85 || c.pc_disk > 92) ? 'warn' : 'ok', 'Dieser PC',
    'CPU ' + de(c.pc_cpu) + ' % · RAM ' + de(c.pc_ram) + ' %' + (c.pc_vram != null ? ' · VRAM ' + de(c.pc_vram) + ' %' : '')
    + (c.pc_gpu_temp != null ? ' · GPU ' + de(c.pc_gpu_temp) + ' °C' : ''));
  $('#sysList').innerHTML = rows.map(function (r) {
    return '<li class="' + r[0] + '"><i class="dot"></i><span class="nm">' + esc(r[1]) + '</span><em>' + esc(r[2]) + '</em></li>';
  }).join('') || '<p class="hint">lädt …</p>';
  var bad = rows.filter(function (r) { return r[0] !== 'ok'; }).length;
  $('#sysAge').textContent = bad ? bad + ' Hinweis' + (bad > 1 ? 'e' : '') : 'alles grün';
  $('#sysAge').className = bad ? 'warn' : '';
}

/* Homelab tile: what Grafana shows, from Prometheus and Pi-hole (homelab.py). The
   internet bar is the share of the line rate in use right now. */
function renderHomelab(h) {
  var box = $('#labGrid');
  if (!box) return;
  if (!h || h.error) { box.innerHTML = '<p class="hint">' + esc(h ? h.error : 'lädt …') + '</p>'; return; }
  var use = h.line_down ? Math.min(100, (h.down || 0) / h.line_down * 100) : 0;
  var cell = function (cls, value, label, extra) {
    return '<div class="mcell ' + cls + '"><b>' + value + '</b><span>' + esc(label) + '</span>' + (extra || '') + '</div>';
  };
  var net = h.link === 0 ? 'offline' : '↓ ' + de(h.down, 1) + ' · ↑ ' + de(h.up, 1);
  box.innerHTML =
    cell(h.link === 0 ? 'bad' : '', esc(net), 'Internet Mbit/s', '<i class="labbar"><i style="width:' + use.toFixed(1) + '%"></i></i>')
    + cell(h.ph_pct == null ? 'dim' : (h.ph_on === false ? 'bad' : ''),
           h.ph_pct == null ? '—' : de(h.ph_pct, 1) + ' %', h.ph_on === false ? 'Pi-hole AUS' : 'Pi-hole blockiert')
    + cell(h.pi_temp > 70 ? 'warn' : '', de(h.pi_temp, 1) + ' °C', 'Pi Temperatur')
    + cell(h.pi_cpu > 90 ? 'warn' : '', de(h.pi_cpu) + ' %', 'Pi CPU');
  $('#labAge').textContent = h.ph_error ? 'Pi-hole: Passwort fehlt' : 'Prometheus · Pi-hole';
  $('#labAge').className = h.ph_error ? 'warn' : '';
  if (h.ph_error) box.children[1].title = h.ph_error;
}

/* Live strip under the top bar: four cards (CPU, GPU, RAM, network) of this PC, every
   two seconds (sysmon.py). Each has a ring in the style of the house gauges and a
   two-minute area trace; the ring turns amber at 70 %, pink at 90 %. Paused while hidden. */
var LiveBar = {
  hist: { cpu: [], gpu: [], ram: [], down: [], up: [] }, max: 60, R: 15,
  tone: function (v) { return v == null ? 'var(--ink-dim)' : v >= 90 ? 'var(--pink)' : v >= 70 ? 'var(--amber)' : 'var(--cy)'; },
  ring: function (id) {
    var c = 2 * Math.PI * LiveBar.R;
    return '<svg class="lvring" viewBox="0 0 40 40"><circle cx="20" cy="20" r="' + LiveBar.R + '" class="gbase"/>'
      + '<circle id="' + id + '" cx="20" cy="20" r="' + LiveBar.R + '" class="gval" style="stroke-dasharray:' + c.toFixed(1)
      + ';stroke-dashoffset:' + c.toFixed(1) + '"/></svg>';
  },
  card: function (key, label, ring) {
    return '<div class="lv" id="lv_' + key + '">' + (ring ? LiveBar.ring('lvr_' + key) : '')
      + '<div class="lvtext"><span>' + label + '</span><b id="lvv_' + key + '">—</b><em id="lvs_' + key + '"></em></div>'
      + '<canvas id="lvc_' + key + '"></canvas></div>';
  },
  build: function () {
    var bar = $('#liveBar');
    if (!bar) return false;
    bar.innerHTML = LiveBar.card('cpu', 'CPU', true) + LiveBar.card('gpu', 'GPU', true)
      + LiveBar.card('ram', 'RAM', true) + LiveBar.card('net', 'Netz · dieser PC', false);
    return true;
  },
  setRing: function (key, v) {
    var el = document.getElementById('lvr_' + key);
    if (!el) return;
    var c = 2 * Math.PI * LiveBar.R, f = Math.max(0, Math.min(1, (v || 0) / 100));
    el.style.strokeDashoffset = (c * (1 - f)).toFixed(1);
    el.style.stroke = LiveBar.tone(v);
    el.style.filter = 'drop-shadow(0 0 3px ' + LiveBar.tone(v) + ')';
  },
  /* Area trace, sharp on any screen: the canvas takes its CSS size times the pixel ratio. */
  trace: function (key, series) {
    var cv = document.getElementById('lvc_' + key);
    if (!cv || !cv.clientWidth) return;
    var dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    var all = [1];
    series.forEach(function (s) { s.data.forEach(function (x) { if (x != null) all.push(x); }); });
    var top = series[0].scale || Math.max.apply(null, all) * 1.15;
    // faint guides at a quarter, half and three quarters — the trace has a scale to sit on
    g.strokeStyle = 'rgba(95,228,255,.07)'; g.lineWidth = 1;
    [0.25, 0.5, 0.75].forEach(function (f) {
      var gy = Math.round(h - 1 - f * (h - 3)) + 0.5;
      g.beginPath(); g.moveTo(0, gy); g.lineTo(w, gy); g.stroke();
    });
    series.forEach(function (s) {
      var d = s.data, n = d.length;
      if (n < 2) return;
      // the full width from the start; once two minutes are in, it scrolls
      var x = function (i) { return i * (w / (n - 1)); };
      var y = function (v) { return h - 1 - Math.min(1, (v || 0) / top) * (h - 3); };
      var path = function () {
        g.beginPath();
        d.forEach(function (v, i) { if (i) g.lineTo(x(i), y(v)); else g.moveTo(x(i), y(v)); });
      };
      if (s.fill) {
        // the glow starts at the trace's highest point, not at the top of the box: at low
        // load the area would otherwise sit in the transparent end of the gradient
        var peak = Math.min.apply(null, d.map(function (v) { return y(v); }));
        var grad = g.createLinearGradient(0, peak, 0, h);
        grad.addColorStop(0, s.fill);
        grad.addColorStop(1, 'rgba(0,0,0,0.02)');
        path();
        g.lineTo(x(n - 1), h); g.lineTo(x(0), h); g.closePath();
        g.fillStyle = grad; g.fill();
      }
      path();
      g.strokeStyle = s.line; g.lineWidth = 1.4; g.lineJoin = 'round';
      g.stroke();
    });
  },
  push: function (key, v) {
    var h = LiveBar.hist[key];
    h.push(v == null ? null : Number(v));
    if (h.length > LiveBar.max) h.shift();
  },
  text: function (key, value, sub) {
    var v = document.getElementById('lvv_' + key), s = document.getElementById('lvs_' + key);
    if (v) v.innerHTML = value;
    if (s) s.textContent = sub || '';
  },
  tick: async function () {
    if (document.hidden) return;
    try {
      var d = await (await fetch('/api/sysmon', { headers: headers() })).json();
      if (d.error) return;
      var L = LiveBar, pct = function (v) { return v == null ? '—' : de(v) + '<small>%</small>'; };
      L.push('cpu', d.cpu); L.push('gpu', d.gpu); L.push('ram', d.ram); L.push('down', d.net_down); L.push('up', d.net_up);
      L.setRing('cpu', d.cpu); L.setRing('gpu', d.gpu); L.setRing('ram', d.ram);
      L.text('cpu', pct(d.cpu), d.cores ? d.cores + ' Threads' : '');
      L.text('gpu', pct(d.gpu), d.vram_used != null
        ? 'VRAM ' + de(d.vram_used, 1) + ' / ' + de(d.vram_total, 0) + ' GB · ' + de(d.gpu_temp) + ' °C' : '');
      L.text('ram', pct(d.ram), de(d.ram_used, 1) + ' / ' + de(d.ram_total, 0) + ' GB');
      L.text('net', '<i class="dn">↓</i>' + de(d.net_down, 1) + ' <i class="up">↑</i>' + de(d.net_up, 1), 'Mbit/s');
      var cy = 'rgba(95,228,255,.9)', cyf = 'rgba(95,228,255,.28)';
      L.trace('cpu', [{ data: L.hist.cpu, scale: 100, line: cy, fill: cyf }]);
      L.trace('gpu', [{ data: L.hist.gpu, scale: 100, line: 'rgba(185,140,255,.95)', fill: 'rgba(185,140,255,.28)' }]);
      L.trace('ram', [{ data: L.hist.ram, scale: 100, line: 'rgba(46,217,155,.9)', fill: 'rgba(46,217,155,.24)' }]);
      L.trace('net', [{ data: L.hist.down, line: cy, fill: cyf }, { data: L.hist.up, line: 'rgba(255,179,64,.9)' }]);
      var gpuCard = document.getElementById('lv_gpu');
      if (gpuCard) gpuCard.hidden = d.gpu == null;      // no NVIDIA card: nothing to show
      if (gpuCard && d.gpu_name) gpuCard.title = d.gpu_name;
    } catch (e) { /* server restarting — next tick */ }
  },
  start: function () {
    if (!LiveBar.build()) return;
    LiveBar.tick();
    setInterval(LiveBar.tick, 2000);
  }
};

function renderTop(d) {
  var w = d.weather, e = d.event;
  if (w && !w.error) {
    $('#wNow').textContent = de(w.now) + '° · ' + w.sky;
    $('#wSub').textContent = de(w.tmin) + '–' + de(w.tmax) + '° · Regen ' + w.rain + ' % · Wind ' + w.wind;
    $('#gVerdict').textContent = w.golf;
    $('#chipGolf em').textContent = 'Golf ' + (w.golf_day || 'heute');
    $('#chipGolf').className = 'chip-lg ' + w.golf_cls;
  }
  if (e && !e.error) {
    $('#eTitle').textContent = e.title || 'frei';
    $('#eWhen').textContent = e.when;
  }
}

async function loadDashboard() {
  try {
    var res = await fetch('/api/dashboard', { headers: headers() });
    var d = await res.json();
    renderTop(d); renderHouse(d.house); renderMe(d.me); renderMail(d.mail); renderSystems(d); renderHomelab(d.homelab);
    Station.night(!!(d.house && d.house.night));
    if (window.Tablet) Tablet.render(d);
  } catch (e) { /* server restarting — next tick */ }
}

function tickClock() {
  var now = new Date();
  $('#clockTime').textContent = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  $('#clockDate').textContent = now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
  setText('#nightClock', $('#clockTime').textContent);
}

function renderInspector(node, trace) {
  var box = $('#inspector');
  if (!node) {
    box.innerHTML = '<p class="hint">Click a node to focus it — only that node and its '
      + 'connections light up, and you can read its note here. Shift-click a second node '
      + 'to trace the path.</p>';
    return;
  }
  var links = (graph.adj.get(node.id) || []).slice(0, 12);
  var html = '<div class="ititle">' + esc(node.title) + '</div>'
    + '<div class="imeta">' + esc(node.type) + ' · ' + node.degree + ' links'
    + (node.updated ? ' · ' + esc(node.updated) : '') + '</div>'
    + '<div class="ibody">' + esc(node.snippet || 'No body text.') + '</div>';
  if (trace && trace.length > 1) {
    html += '<div class="imeta" style="margin-top:10px">path · ' + trace.length + ' hops</div>'
      + '<div class="ibody">' + trace.map(function (id) {
        var n = graph.byId.get(id); return esc(n ? n.title : id);
      }).join(' → ') + '</div>';
  }
  if (links.length) {
    html += '<div class="ilinks">' + links.map(function (id) {
      var n = graph.byId.get(id);
      return '<span class="chip" data-id="' + esc(id) + '">' + esc(n ? n.title : id) + '</span>';
    }).join('') + '</div>';
  }
  box.innerHTML = html;
  Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (c) {
    c.onclick = function () { focusNode(c.dataset.id); };
  });
}

function focusNode(id) {
  if (graph.setFocus(id, false)) {
    log('mem', 'MEMORY', 'focused ' + id);
  }
}

/* ── data ─────────────────────────────────── */
async function loadGraph(force) {
  var res = await fetch('/api/graph' + (force ? '?force=1' : ''), { headers: headers() });
  GRAPH = await res.json();
  graph.setData(GRAPH);
  $('#stageCount').textContent = GRAPH.total + ' notes · ' + GRAPH.links.length + ' links';
  setTimeout(function () { graph.fit(); }, 350);
}

/* Live state from Home Assistant: offline devices fade, levels glow. */
async function loadLive() {
  try {
    graph.setLive(await (await fetch('/api/live', { headers: headers() })).json());
  } catch (e) { /* HA or server unreachable: stars keep their last state */ }
}

/* Server gone (restart, PC asleep, Wi-Fi): a banner, a quick retry every few
   seconds instead of the 20 s poll, and a fresh page once it answers again —
   the old page holds a dead token and event stream. The service worker (sw.js)
   covers the case of a reload landing in the gap. */
var Reconnect = {
  since: 0, timer: 0,
  lost: function () {
    if (!Reconnect.since) {
      Reconnect.since = Date.now();
      document.body.classList.add('offline');
    }
    clearTimeout(Reconnect.timer);
    Reconnect.timer = setTimeout(loadStatus, 3000);
  },
  /* true = the page reloads, the caller stops */
  back: function (boot) {
    if (!Reconnect.since) return false;
    if (!window.__boot || boot === window.__boot) {   // only a hiccup, same server (or first contact)
      Reconnect.since = 0;
      document.body.classList.remove('offline');
      return false;
    }
    location.reload();
    return true;
  }
};

/* Back in the app on the iPad or iPhone: iOS takes the microphone away while the app is
   in the background (the track ends, the audio context is interrupted), yet the page still
   believed it was listening and heard nothing. A fresh page is the reliable way back —
   mic, wake word and token anew. A glance at the notification centre (under 2 s) is no
   reason; the PC's own tab is never reloaded, its desktop card covers the hidden tab. */
var Resume = { hiddenAt: 0 };
document.addEventListener('visibilitychange', function () {
  if (!window.__remote) return;
  if (document.visibilityState === 'hidden') { Resume.hiddenAt = Date.now(); return; }
  if (Resume.hiddenAt && Date.now() - Resume.hiddenAt > 2000) location.reload();
});
window.addEventListener('pageshow', function (e) { if (e.persisted && window.__remote) location.reload(); });

/* Each display step is guarded on its own: one missing element (the layout
   changes) must never stop the rest — that once left __serverSTT unset and
   the Live button silently refusing to start. */
function setText(sel, text) { var el = $(sel); if (el) el.textContent = text; }
function safely(fn) { try { fn(); } catch (e) { console.warn('[jarvis] status display:', e); } }

async function loadStatus() {
  var s;
  try {
    var res = await fetch('/api/status', { headers: headers() });
    if (res.status === 401) { location.href = '/pair.html'; return; }   // this device was removed
    s = await res.json();   // Tailscale's 502 page while the server restarts is no JSON
  } catch (e) { setText('#pGateway', 'offline'); Reconnect.lost(); return; }
  if (Reconnect.back(s.boot)) return;
  window.__remote = !!s.remote;
  document.body.classList.toggle('remote', !!s.remote);
  // The server restarted after a code change: reload for the new code and token,
  // but never mid-conversation — otherwise try again on the next poll.
  if (window.__boot && s.boot && s.boot !== window.__boot) {
    if (!running && !Live.speaking && !Live.sending && !Live.armed && !Speech.active && !Ringing.on) location.reload();
    return;
  }
  window.__boot = s.boot;
  // Capabilities first — the mic and voice depend on these.
  window.__serverVoice = s.server_voice;
  window.__serverSTT = s.server_stt;
  window.__voiceEngine = s.voice_engine || null;   // the server-side engine (voice-client.js) replaces Live when it runs
  safely(function () { setText('#pGateway', s.remote ? 'remote' : 'online :' + location.port); });
  safely(function () { setText('#pBrain', s.runtime === 'claude' ? (s.version || 'Claude CLI') : 'MOCK'); });
  safely(function () { setText('#pVoice', s.voice_name || (s.server_voice ? 'Fish Audio' : 'browser')); });
  safely(function () {
    var dot = $('#pVoice') && $('#pVoice').parentNode.querySelector('.dot');
    if (dot) dot.className = 'dot ' + (s.server_voice ? 'ok' : 'warn');
  });
  safely(function () { setText('#sModel', s.model); });
  safely(function () { setText('#cliBadge', s.runtime === 'claude' ? 'CLAUDE CLI' : 'CLI OFFLINE'); });
  safely(function () { if (s.usage) renderUsage(s.usage); });
  safely(function () { if (s.billing) renderBilling(s.billing); });
  safely(function () { if (s.tts) renderTts(s.tts); });
  safely(function () { if (s.listener) window.__listener = s.listener; });
  safely(function () { if (s.wake) Wake.configure(s.wake); });
  safely(function () { Ambience.house(s.ambience); });
  safely(function () { Output.update(s.sonos); });
  safely(function () {
    window.__quiet = !!s.quiet;
    if (s.persona) { window.__persona = s.persona; if (!running) $('#toneNow').textContent = s.persona; }
    if (typeof s.night_volume === 'number') window.__nightVolume = s.night_volume;
    if (typeof s.voice_factor === 'number') window.__voiceFactor = s.voice_factor;
  });
  safely(function () { if (s.timers) Timers.set(s.timers); });
  safely(function () {
    var f = s.fallback, el = $('#pBrain');
    if (el && f && f.mode !== 'off') el.title = 'Ersatz bei Claude-Ausfall: ' + (f.chain || []).map(function (r) {
      return r.name + ' ' + r.model + (r.ok ? ' (bereit)' : ' — ' + r.why);
    }).join(' → ');
  });
  updateMicButton();
}

/* ── usage panel ───────────────────────────────
   Claude Code reports the subscription limits with every answer (5-hour and
   weekly window, extra-usage credit). The API credit balance itself has no
   public endpoint, so that one is a link to the Console. */
function fmtReset(ts) {
  if (!ts) return '';
  var d = new Date(ts * 1000), now = new Date();
  var hm = d.toTimeString().slice(0, 5);
  if (d.toDateString() === now.toDateString()) return '↻ ' + hm;
  return '↻ ' + ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'][d.getDay()] + ' ' + hm;
}

function setBar(id, win) {
  var bar = $('#' + id), val = $('#' + id + 'v'), rst = $('#' + id + 'r');
  if (!bar) return;
  if (!win || typeof win.utilization !== 'number') { val.textContent = '—'; return; }
  var pct = Math.round(win.utilization * 100);
  bar.style.width = Math.min(100, pct) + '%';
  bar.className = pct >= 90 ? 'crit' : (pct >= 70 ? 'warn' : '');
  val.textContent = pct + '%';
  rst.textContent = fmtReset(win.resetsAt);
}

function renderUsage(u) {
  if (!u || !$('#u5')) return;
  setBar('u5', u.five_hour);
  setBar('u7', u.seven_day);
  var extra = $('#uExtra');
  if (u.using_overage) { extra.textContent = 'wird genutzt'; extra.className = ''; }
  else if (u.overage_reason === 'out_of_credits') { extra.textContent = 'leer'; extra.className = 'empty'; }
  else if (u.overage === 'allowed') { extra.textContent = 'verfügbar'; extra.className = ''; }
  else { extra.textContent = u.overage_reason ? u.overage_reason.replace(/_/g, ' ') : 'aus'; extra.className = ''; }
  var mins = Math.round((Date.now() / 1000 - (u.at || 0)) / 60);
  $('#usageAge').textContent = mins < 1 ? 'gerade' : 'vor ' + mins + ' min';
}

/* Voice provider chain: who speaks now, what is left, who is paused until when. */
function renderTts(t) {
  var el = $('#uTts'), sub = $('#uTtsSub'), row = $('#uTtsRow');
  if (!el || !t) return;
  var active = (t.chain || []).filter(function (c) { return c.name === t.active; })[0];
  el.textContent = (t.active_label || '—') + (active && active.balance ? ' · ' + active.balance : '');
  var paused = (t.chain || []).filter(function (c) { return c.blocked_until; }).map(function (c) {
    var d = new Date(c.blocked_until * 1000);
    return c.label + ' leer bis ' + d.getDate() + '.' + (d.getMonth() + 1) + '.';
  });
  sub.textContent = paused.join(' · ');
  row.title = 'Stimmen-Kette:\n' + (t.chain || []).map(function (c, i) {
    return (i + 1) + '. ' + c.label + ': ' + (!c.configured ? 'kein Key' : c.blocked_until ? 'gesperrt (' + c.reason + ')'
      : 'bereit') + (c.balance ? ' — ' + c.balance : '');
  }).join('\n');
}

function usd(v) { return '$' + Number(v).toFixed(2); }

function renderBilling(b) {
  var el = $('#uBal'), sub = $('#uBalSub');
  if (!el || !b) return;
  el.className = '';
  if (!b.balance_set) {
    el.textContent = 'eintragen ✎';
    sub.textContent = b.configured ? '' : 'Für automatische Abzüge JARVIS_COST_KEY in .env setzen';
  } else if (typeof b.remaining === 'number') {
    el.textContent = usd(b.remaining);
    if (b.remaining < 2) el.className = 'crit';
    else if (b.remaining < 5) el.className = 'warn';
    var parts = [];
    if (typeof b.today === 'number') parts.push('heute ' + usd(b.today));
    if (typeof b.month === 'number') parts.push('Monat ' + usd(b.month));
    if (b.note) parts.push(b.note);
    sub.textContent = parts.join(' · ');
  } else {
    el.textContent = usd(b.entered) + '?';
    sub.textContent = b.error || '';
  }
  if (b.error && b.balance_set && typeof b.remaining !== 'number') sub.textContent = b.error;
}

(function initBalance() {
  var btn = $('#uBalEdit'), form = $('#uBalForm'), input = $('#uBalInput');
  if (!btn) return;
  var open = function () { form.hidden = !form.hidden; if (!form.hidden) input.focus(); };
  btn.onclick = open;
  $('#uBal').onclick = open;
  form.onsubmit = async function (e) {
    e.preventDefault();
    var res = await fetch('/api/balance', {
      method: 'POST', headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ usd: input.value })
    });
    var data = await res.json();
    if (!res.ok) { log('error', 'GUTHABEN', data.error || 'Fehler'); return; }
    input.value = ''; form.hidden = true;
    renderBilling(data);
    log('ok', 'GUTHABEN', 'Kontostand gesetzt: ' + usd(data.entered));
  };
})();

/* ── the run ──────────────────────────────── */
// A typed question may get a long answer: only its first paragraph is read aloud.
var typedRun = false;
function speakable(text) {
  if (!typedRun) return text;
  var cut = text.search(/\n\s*\n/);
  return cut >= 0 ? text.slice(0, cut) : text;
}

async function transmit(message, opts) {
  message = (message || '').trim();
  if (!message) return;
  opts = opts || {};
  if (typeof VoiceClient !== 'undefined' && VoiceClient.on && VoiceClient.ready) {
    // the engine answers and speaks; its events come back through VoiceClient.onEvent
    bubble('me', message);
    VoiceClient.send(message, !!opts.typed, opts.speak !== false);
    return;
  }
  if (running) { log('note', 'BUSY', 'still working — "' + message.slice(0, 36) + '" not sent'); return; }
  running = true;
  interruptSpeech();   // a new question ends whatever he was still saying
  speechReset();
  streamSpeak = opts.speak !== false;
  answerText = '';
  document.body.classList.remove('boot');
  document.body.classList.add('running');
  if (graph) graph.setActivity(1);
  $('#toneNow').textContent = window.__persona || '—';
  typedRun = !!opts.typed;
  // typed with a Sonos answering: music down now, as with "Jarvis" — the answer comes out of the room
  if (typedRun && streamSpeak && Output.target) MusicHold.hold();
  setVoiceState('THINKING', 'hot');
  bubble('me', message);
  answerBubble = bubble('jarvis', '');
  answerBubble.classList.add('thinking');
  log('run', 'RUN', message.slice(0, 70));

  var t0 = performance.now();
  try {
    activeCtl = new AbortController();
    var res = await fetch('/api/run', {
      method: 'POST',
      headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ message: message, fresh: /^\/new\b/.test(message), typed: !!opts.typed }),
      signal: activeCtl.signal
    });
    if (!res.ok) throw new Error('backend HTTP ' + res.status);
    var reader = res.body.getReader(), dec = new TextDecoder(), buf = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      var nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        var line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
  } catch (e) {
    if (e && e.name === 'AbortError') { log('note', 'CANCEL', 'run cancelled'); opts.speak = false; }
    else { log('error', 'ERROR', String(e).slice(0, 160)); answerBubble.textContent = String(e).slice(0, 160); }
  }
  activeCtl = null;
  running = false;
  document.body.classList.remove('running');
  if (graph) graph.setActivity(0);
  answerBubble.classList.remove('thinking');
  $('#sLatency').textContent = Math.round(performance.now() - t0) + 'ms';
  setVoiceState('IDLE', '');
  if (answerText.trim() && opts.speak !== false) {
    feedSpeech(speakable(answerText), true);   // whatever is left after the last full sentence
    await speechDrained();
  }
  streamSpeak = false;
}

/* Stop talking and drop the rest of the answer (Escape, or talking over him).
   running is cleared right away so live mode can record the interruption. */
function cancelRun() {
  if (typeof VoiceClient !== 'undefined' && VoiceClient.on) { VoiceClient.stopAll('Abbruch'); running = false; return; }
  interruptSpeech();
  streamSpeak = false;
  if (activeCtl) activeCtl.abort();
  running = false;
  fetch('/api/cancel', { method: 'POST', headers: headers({ 'content-type': 'application/json' }), body: '{}' });
}

function handleEvent(ev) {
  switch (ev.t) {
    case 'status':
      if (ev.session_id) $('#sSession').textContent = ev.session_id.slice(0, 8);
      if (ev.model) $('#sModel').textContent = ev.model;
      log('ok', 'STATUS', 'claude online · tools=' + (ev.tools || 0)
        + ' · permission=' + (ev.permission || '—'));
      break;
    case 'latency':
      $('#sLatency').textContent = ev.ms + 'ms';
      log('note', 'LATENCY', 'first token after ' + ev.ms + 'ms');
      break;
    case 'tool':
      if (ev.phase === 'use') log('tool', 'TOOL', '→ ' + ev.name + '(' + (ev.input || '') + ')');
      else log('tool', 'TOOL', '✓ ' + (ev.ok === false ? 'error' : 'ok'));
      break;
    case 'focus':
      if (graph.setFocus(ev.id, false)) log('mem', 'MEMORY', 'vault hit → ' + ev.id);
      break;
    case 'graph':
      loadGraph(true);
      break;
    case 'delta':
      answerText += ev.text;
      if (streamSpeak) feedSpeech(speakable(answerText), false);
      renderSpoken(answerBubble, answerText);
      var tone = lastTone(answerText);
      if (tone) $('#toneNow').textContent = tone;
      $('#transcript').scrollTop = $('#transcript').scrollHeight;
      break;
    case 'route':
      $('#routeNow').textContent = ev.model + (ev.groups && ev.groups.length ? ' · ' + ev.groups.join(', ') : '')
        + (ev.reason ? ' · ' + ev.reason : '');
      break;
    case 'ratelimit':
      $('#sLimit').textContent = (ev.status || '—') + (ev.window ? ' · ' + ev.window.replace('_', ' ') : '');
      renderUsage(Object.assign({ at: Date.now() / 1000 }, ev));
      break;
    case 'note':
      log('note', 'NOTE', ev.message || '');
      break;
    case 'error':
      log('error', 'ERROR', ev.message || '');
      answerBubble.textContent = ev.message || 'error';
      break;
    case 'confirm':
      showConfirm(ev);
      break;
    case 'confirm_done':
      closeConfirm(ev.id, ev.allow);
      break;
    case 'complete':
      if (ev.session_id) $('#sSession').textContent = String(ev.session_id).slice(0, 8);
      log('ok', 'DONE', (ev.ms || 0) + 'ms');
      break;
  }
}

/* ── confirmation gate ─────────────────────
   Shell commands and writes outside the Jarvis folder only run after the user
   confirms here: a click, or saying "bestätigt" / "nein". No answer = denied. */
var pendingConfirm = null;

function answerConfirm(allow) {
  if (!pendingConfirm) return;
  var id = pendingConfirm.id;
  fetch('/api/permission/answer', {
    method: 'POST', headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ id: id, allow: !!allow })
  });
  closeConfirm(id, allow);
}

function showConfirm(ev) {
  var card = document.createElement('div');
  card.className = 'bubble confirm';
  card.innerHTML = '<div class="ctitle">Bestätigung erforderlich · ' + esc(ev.tool) + '</div>'
    + '<pre class="cdetail">' + esc(ev.detail || '') + '</pre>'
    + '<div class="cbtns"><button class="cyes">Bestätigen</button>'
    + '<button class="cno">Ablehnen</button><span class="ctimer"></span></div>';
  $('#transcript').appendChild(card);
  $('#transcript').scrollTop = $('#transcript').scrollHeight;
  var left = ev.timeout || 75;
  var timer = setInterval(function () {
    left -= 1;
    card.querySelector('.ctimer').textContent = 'automatisch abgelehnt in ' + left + ' s';
    if (left <= 0) clearInterval(timer);
  }, 1000);
  pendingConfirm = { id: ev.id, card: card, timer: timer };
  card.querySelector('.cyes').onclick = function () { answerConfirm(true); };
  card.querySelector('.cno').onclick = function () { answerConfirm(false); };
  log('note', 'FREIGABE', ev.tool + ': ' + String(ev.detail || '').slice(0, 80));
  setVoiceState('BESTÄTIGEN?', 'hot');
  speak('[serious] Bestätigung erforderlich, Sir.');
}

function closeConfirm(id, allow) {
  if (!pendingConfirm || pendingConfirm.id !== id) return;
  clearInterval(pendingConfirm.timer);
  var card = pendingConfirm.card;
  card.classList.add(allow ? 'approved' : 'denied');
  card.querySelector('.cbtns').innerHTML = allow ? '✓ bestätigt' : '✕ abgelehnt';
  pendingConfirm = null;
  log(allow ? 'ok' : 'note', 'FREIGABE', allow ? 'bestätigt' : 'abgelehnt');
  if (allow) approvalBeep();
  if (running) setVoiceState('THINKING', 'hot');
}

/* Short checkout-scanner beep when a confirmation goes through. */
var beepCtx = null;
/* Beeps go through the microphone's context when it runs: on the iPad a context of its
   own stays shut until a tap, the mic's one is already open. */
function chimeCtx() {
  var live = liveOutCtx();
  if (live) return live;
  beepCtx = beepCtx || new (window.AudioContext || window.webkitAudioContext)();
  if (beepCtx.state !== 'running') beepCtx.resume();
  return beepCtx;
}
function approvalBeep() {
  try {
    var bc = chimeCtx();
    var t = bc.currentTime + 0.02;
    var osc = bc.createOscillator(), gain = bc.createGain();
    osc.type = 'square';
    osc.frequency.value = 1800;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.08, t + 0.005);
    gain.gain.setValueAtTime(0.08, t + 0.12);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
    osc.connect(gain); gain.connect(bc.destination);
    osc.start(t); osc.stop(t + 0.16);
  } catch (e) { /* no audio, no beep */ }
}

/* ── voice ────────────────────────────────── */
/* stopSpeaking is set while JARVIS talks; calling it cuts him off mid-sentence
   (voice barge-in, Esc or Space). */
var stopSpeaking = null;

function interruptSpeech() {
  if (typeof VoiceClient !== 'undefined' && VoiceClient.on) {
    if (!Live.speaking && !running) return false;
    VoiceClient.stopAll('unterbrochen');
    return true;
  }
  if (!stopSpeaking && !Speech.active) return false;
  Speech.cancelled = true;
  if (Speech.active || Speech.paused) sonosHalt(false);   // the Sonos stops too, and nothing waits there
  Speech.paused = false;
  if (Speech.onResume) { var go = Speech.onResume; Speech.onResume = null; go(); }
  // sentences already requested: stop their download, the provider need not finish them
  Speech.queue.forEach(function (it) { it.blob.then(function (v) { if (v && v.cancel) v.cancel(); }); });
  Speech.queue = [];
  if (!stopSpeaking) return true;
  var stop = stopSpeaking;
  stopSpeaking = null;
  stop();
  log('note', 'VOICE', 'unterbrochen');
  return true;
}

/* Feed JARVIS' own voice level into the orb while he talks. */
var orbAudioCtx = null;
function driveOrb(audio, isDone) {
  if (!window.JarvisOrb) return;
  try {
    orbAudioCtx = orbAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    // Routing through a suspended context would mute JARVIS — only tap the
    // audio once the context is actually running, otherwise skip the effect.
    if (orbAudioCtx.state !== 'running') { orbAudioCtx.resume(); return; }
    var src = orbAudioCtx.createMediaElementSource(audio);
    var an = orbAudioCtx.createAnalyser();
    an.fftSize = 512;
    src.connect(an); an.connect(orbAudioCtx.destination);
    Wave.speech = an;
    var buf = new Uint8Array(an.fftSize);
    (function tick() {
      if (isDone()) { JarvisOrb.setLevel(0); if (Wave.speech === an) Wave.speech = null; return; }
      an.getByteTimeDomainData(buf);
      var sum = 0;
      for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
      JarvisOrb.setLevel(Math.sqrt(sum / buf.length) * 4);
      requestAnimationFrame(tick);
    })();
  } catch (e) { /* audio still plays through the element */ }
}

/* Waveform in the right rail: Jarvis' voice while he speaks, otherwise the live microphone.
   Only real signal — a flat line when nothing is there. */
var Wave = { speech: null, cv: null };
(function waveLoop() {
  var cv = document.getElementById('wave');
  if (!cv) return;
  var g = cv.getContext('2d'), buf = new Uint8Array(512);
  (function draw() {
    var an = Wave.speech || (typeof Live !== 'undefined' && Live.on && Live.analyser) || null;
    g.clearRect(0, 0, cv.width, cv.height);
    g.strokeStyle = Wave.speech ? 'rgba(95,228,255,.95)' : 'rgba(95,228,255,.45)';
    g.lineWidth = 1.5;
    g.beginPath();
    if (an) {
      var n = Math.min(buf.length, an.fftSize || 512);
      an.getByteTimeDomainData(buf.subarray(0, n));
      for (var i = 0; i < n; i++) {
        var x = i / (n - 1) * cv.width, y = buf[i] / 255 * cv.height;
        if (i) g.lineTo(x, y); else g.moveTo(x, y);
      }
    } else { g.moveTo(0, cv.height / 2); g.lineTo(cv.width, cv.height / 2); }
    g.stroke();
    requestAnimationFrame(draw);
  })();
})();

/* Notification centre behind the bell: messages, homelab incidents, open proposals. */
var Notes = {
  unseen: 0,
  bump: function () {
    Notes.unseen++;
    var b = $('#bellCount'); if (b) { b.hidden = false; b.textContent = Notes.unseen > 9 ? '9+' : Notes.unseen; }
  },
  open: async function () {
    Notes.unseen = 0; $('#bellCount').hidden = true;
    $('#noteBox').hidden = false;
    try {
      var n = await (await fetch('/api/notifications', { headers: headers() })).json();
      var t = function (s) { return new Date(s * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); };
      var html = '';
      if (n.paused_until) html += '<h3>NOT-AUS</h3><ul><li class="bad">Pausiert bis ' + t(n.paused_until) + ' — „weitermachen“ hebt es auf.</li></ul>';
      if (n.proposals.length) html += '<h3>OFFENE VORSCHLÄGE</h3><ul>' + n.proposals.map(function (p) {
        return '<li class="prop"><b>' + esc(p.title) + '</b><br>' + esc(p.why) + '</li>'; }).join('') + '</ul>';
      if (n.tasks && n.tasks.length) html += '<h3>AUFGABEN</h3><ul>' + n.tasks.map(function (t) {
        var cls = t.status === 'COMPLETED' ? 'ok' : (/FAIL|TIMED|CANCEL/.test(t.status) ? 'bad' : 'prop');
        return '<li class="' + cls + '"><small>' + esc(t.status) + ' · ' + t.done + '/' + t.total + '</small><b>'
          + esc(t.title) + '</b>' + (t.report ? '<br>' + esc(t.report) : '') + '</li>'; }).join('') + '</ul>';
      html += await Notes.proactive(t);
      html += '<h3>MELDUNGEN</h3><ul>' + (n.items.map(function (e) {
        return '<li><small>' + t(e.at) + ' · ' + esc(e.kind) + '</small>' + esc(e.text) + '</li>'; }).join('')
        || '<li>Noch keine Meldungen seit dem Start.</li>') + '</ul>';
      if (n.incidents.length) html += '<h3>HOMELAB</h3><ul>' + n.incidents.map(function (i) {
        return '<li class="' + (i.state === 'ok' ? 'ok' : 'bad') + '"><small>' + esc(i.at) + '</small>' + esc(i.text) + '</li>'; }).join('') + '</ul>';
      $('#noteBody').innerHTML = html;
      $('#noteAge').textContent = 'Profil ' + (n.persona || '—');
    } catch (e) { $('#noteBody').innerHTML = '<p class="hint">Nicht erreichbar.</p>'; }
  },
  /* The proactive mode: what Jarvis said by himself, what he kept back and why, what he learned from "unwichtig". */
  proactive: async function (t) {
    var p;
    try { p = await (await fetch('/api/proactive', { headers: headers() })).json(); } catch (e) { return ''; }
    if (!p || !p.enabled) return '';
    var html = '<h3>ANSAGEN · PROAKTIV</h3><ul>';
    if (p.quiet_until) html += '<li class="held">Ruhe bis ' + t(p.quiet_until) + ' — nur Kritisches.</li>';
    Object.keys(p.down || {}).forEach(function (k) {
      html += '<li class="bad"><small>Quelle aus</small>' + esc(k) + ': ' + esc(p.down[k].error) + '</li>'; });
    (p.learned || []).forEach(function (l) {
      html += '<li class="prop"><small>gelernt</small>' + esc(l.name) + ': ' + l.marks + '× unwichtig → ' + esc(l.level)
        + (l.level !== l.base ? ' (statt ' + esc(l.base) + ')' : '')
        + '<button class="pa" data-reset="' + esc(l.kind) + '">zurücksetzen</button></li>'; });
    var rows = (p.history || []).filter(function (r) { return r.action !== 'briefing'; }).slice(0, 25);
    html += rows.map(function (r) {
      var cls = r.action === 'gesprochen' ? 'ok' : (r.action === 'zurückgehalten' ? 'held' : '');
      var btn = r.kind && r.level && r.level !== 'kritisch' && r.action !== 'info'
        ? '<button class="pa" data-kind="' + esc(r.kind) + '">unwichtig</button>' : '';
      return '<li class="' + cls + '"><small>' + t(r.at) + ' · ' + esc(r.action) + (r.level ? ' · ' + esc(r.level) : '')
        + (r.room ? ' · ' + esc(r.room) : '') + '</small>' + esc(r.text)
        + (r.reason ? ' <span class="pa-why">(' + esc(r.reason) + ')</span>' : '') + btn + '</li>';
    }).join('') || '<li>Noch keine Ansagen.</li>';
    return html + '</ul>';
  }
};
document.addEventListener('click', async function (e) {
  var b = e.target.closest && e.target.closest('button.pa');
  if (!b) return;
  var body = b.dataset.reset ? { reset: true, kind: b.dataset.reset } : { kind: b.dataset.kind };
  b.disabled = true;
  try {
    await fetch('/api/proactive/feedback', { method: 'POST', body: JSON.stringify(body),
      headers: headers({ 'Content-Type': 'application/json' }) });
    b.textContent = b.dataset.reset ? 'zurückgesetzt' : 'notiert';
  } catch (err) { b.textContent = 'Fehler'; }
});
(function initNotes() {
  var bell = document.getElementById('btnBell');
  if (!bell) return;
  bell.onclick = Notes.open;
  $('#noteClose').onclick = function () { $('#noteBox').hidden = true; };
  $('#noteBox').onclick = function (e) { if (e.target.id === 'noteBox') $('#noteBox').hidden = true; };
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') $('#noteBox').hidden = true; });
})();

/* Views: ?view=wall (command centre, e.g. a second monitor) or ?view=mini; remembered. */
(function initViews() {
  var set = function (v) {
    document.body.classList.remove('view-wall', 'view-mini');
    if (v) document.body.classList.add('view-' + v);
    try { localStorage.setItem('jarvis.view', v || ''); } catch (e) { /* private mode */ }
    ['Wall', 'Mini'].forEach(function (k) { var b = document.getElementById('btn' + k); if (b) b.classList.toggle('on', v === k.toLowerCase()); });
  };
  var q = new URLSearchParams(location.search).get('view'), saved = '';
  try { saved = localStorage.getItem('jarvis.view') || ''; } catch (e) { /* private mode */ }
  set(q !== null ? q : saved);
  window.setView = set;   // the station switches to the command centre
  var wall = document.getElementById('btnWall'), mini = document.getElementById('btnMini');
  if (wall) wall.onclick = function () {
    var on = !document.body.classList.contains('view-wall');
    set(on ? 'wall' : '');
    if (on && document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(function () {});
    else if (!on && document.fullscreenElement) document.exitFullscreen();
  };
  if (mini) mini.onclick = function () { set(document.body.classList.contains('view-mini') ? '' : 'mini'); };
})();

/* Output volume, 0–1 — set by the slider in the right rail, remembered across reloads. */
var voiceVolume = (function () {
  var v = parseFloat(localStorage.getItem('jarvis.volume'));
  return isNaN(v) ? 0.6 : Math.min(1, Math.max(0, v));
})();
var currentAudio = null;
(function initVolume() {
  var slider = document.getElementById('volume');
  var label = document.getElementById('volumeVal');
  if (!slider) return;
  slider.value = Math.round(voiceVolume * 100);
  label.textContent = slider.value;
  slider.addEventListener('input', function () {
    voiceVolume = slider.value / 100;
    label.textContent = slider.value;
    localStorage.setItem('jarvis.volume', String(voiceVolume));
    if (currentAudio) currentAudio.volume = outVolume();
    if (Sound.gain) Sound.gain.gain.value = outVolume();
  });
})();

/* What actually comes out: the slider, softer during the quiet hours (JARVIS_NIGHT_VOLUME), and a
   little softer late in the evening or after a bad night (server: voice_factor). */
function outVolume() {
  var factor = typeof window.__voiceFactor === 'number' ? window.__voiceFactor : 1;
  return voiceVolume * (window.__quiet ? (window.__nightVolume == null ? 0.5 : window.__nightVolume) : 1) * factor;
}

/* Spotify quieter while he talks (server: pc.duck). Released a moment after the
   last sentence, so the music does not pump between sentences. pc.duck only reaches the
   Spotify app on this PC, so every utterance also holds the account's playing device
   (spotify.hold) — his own notices too, not just answers after "Jarvis". MusicHold lets go
   by itself once he is done; a hold already running for an answer is simply kept. */
var Duck = { on: false, timer: null };
function duckMusic(on) {
  clearTimeout(Duck.timer);
  if (on) MusicHold.hold();
  var send = function (v) {
    if (Duck.on === v) return;
    Duck.on = v;
    fetch('/api/duck', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ on: v }) }).catch(function () {});
  };
  if (on) send(true);
  else Duck.timer = setTimeout(function () { if (!Speech.active && !Live.speaking) send(false); }, 900);
}

/* Spotify paused on the wake word (server: spotify.hold), on whatever device of the account plays —
   the phone on the living room speaker too — so the request is heard clearly. It plays on once he is
   done: answered, spoken, and the listening window shut. The server only plays on what it paused. */
var MusicHold = { on: false, timer: null, quiet: 0, since: 0, waited: {} };
MusicHold.send = function (v) {
  MusicHold.on = v;
  // what kept the music paused, for logs/spotify.log — the HUD's own log is out of reach on the iPad
  var why = Object.keys(MusicHold.waited).join(', ');
  fetch('/api/musichold', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ on: v, why: v ? '' : (why || '-') + (document.hidden ? ', Tab verdeckt' : '') }) })
    .catch(function () {});
};
MusicHold.hold = function () {
  if (!MusicHold.on) { MusicHold.send(true); MusicHold.since = performance.now(); MusicHold.waited = {}; }
  MusicHold.quiet = 0;
  clearInterval(MusicHold.timer);
  MusicHold.timer = setInterval(MusicHold.check, 1000);
};
MusicHold.check = function () {
  var busy = { Fenster: Wake.mode() !== 'off' && Wake.awake(), denkt: running, Aufnahme: Live.armed,
    versteht: Live.sending, spricht: Live.speaking || Speech.active, Freigabe: !!pendingConfirm };
  var any = false;
  Object.keys(busy).forEach(function (k) { if (busy[k]) { MusicHold.waited[k] = 1; any = true; } });
  if (any) { MusicHold.quiet = 0; return; }
  if (++MusicHold.quiet < 2) return;           // a breath after the last word, or it pumps between turns
  clearInterval(MusicHold.timer);
  MusicHold.timer = null;
  MusicHold.send(false);
};

/* Runs fn once on the next click/key — browsers only unlock audio after one. */
function afterGesture(fn) {
  var go = function () {
    window.removeEventListener('pointerdown', go, true);
    window.removeEventListener('keydown', go, true);
    fn();
  };
  window.addEventListener('pointerdown', go, true);
  window.addEventListener('keydown', go, true);
}

/* iPad/iPhone: an <audio> element may only start after a tap, and after every return to
   the app the page is fresh again — so he answered silently until someone touched the
   screen. The microphone's AudioContext is already running then and may play: once an
   element was refused, his voice goes through that context (decoded whole, so a stream
   loses its head start — about a second, still better than silence). */
var Sound = { blocked: false, gain: null };

function liveOutCtx() {
  return typeof Live !== 'undefined' && Live.ctx && Live.ctx.state === 'running' ? Live.ctx : null;
}

function playOnCtx(blob, ctx, setStop) {
  return blob.arrayBuffer().then(function (buf) {
    // the callback form: older Safari has no promise from decodeAudioData
    return new Promise(function (ok, fail) { ctx.decodeAudioData(buf, ok, fail); });
  }).then(function (audioBuf) {
    return new Promise(function (resolve) {
      var src = ctx.createBufferSource(), gain = ctx.createGain(), an = ctx.createAnalyser();
      src.buffer = audioBuf;
      gain.gain.value = outVolume();
      an.fftSize = 512;
      src.connect(gain); gain.connect(ctx.destination); gain.connect(an);
      Sound.gain = gain;
      var over = false;
      var end = function () {
        if (over) return;
        over = true;
        if (Sound.gain === gain) Sound.gain = null;
        if (Wave.speech === an) Wave.speech = null;
        if (window.JarvisOrb) JarvisOrb.setLevel(0);
        resolve();
      };
      src.onended = end;
      setStop(function () { try { src.stop(); } catch (e) { /* not started */ } end(); });
      Wave.speech = an;
      if (window.JarvisOrb) {
        var data = new Uint8Array(an.fftSize);
        (function tick() {
          if (over) return;
          an.getByteTimeDomainData(data);
          var sum = 0;
          for (var i = 0; i < data.length; i++) { var v = (data[i] - 128) / 128; sum += v * v; }
          JarvisOrb.setLevel(Math.sqrt(sum / data.length) * 4);
          requestAnimationFrame(tick);
        })();
      }
      src.start();
    });
  });
}

function playBlob(blob) {
  return playUrl(URL.createObjectURL(blob), null, null, function () { return Promise.resolve(blob); });
}

/* A clip that is still arriving (see fetchVoice): fed into a MediaSource chunk by
   chunk, so he starts talking ~2 s before the provider has finished the sentence. */
function playStream(vs) {
  var ms = new MediaSource();
  return playUrl(URL.createObjectURL(ms), function (isFinished) {
    ms.addEventListener('sourceopen', function () {
      var sb;
      try { sb = ms.addSourceBuffer('audio/mpeg'); } catch (e) { return; }
      var i = 0;
      var pump = function () {
        if (isFinished() || sb.updating || ms.readyState !== 'open') return;
        if (i < vs.chunks.length) { try { sb.appendBuffer(vs.chunks[i++]); } catch (e) { /* closed meanwhile */ } return; }
        if (vs.done) { try { ms.endOfStream(); } catch (e) { /* already ended */ } }
      };
      sb.addEventListener('updateend', pump);
      vs.notify = pump;
      pump();
    }, { once: true });
  }, function () { if (!vs.done) vs.cancel(); }, function () {
    return new Promise(function (resolve) {
      var check = function () { if (vs.done) { vs.notify = null; resolve(new Blob(vs.chunks, { type: vs.mime })); } };
      vs.notify = check;
      check();
    });
  });
}

/* Whatever fetchVoice produced: a finished blob, a stream, or a stream in a format
   MediaSource cannot take (the local voice sends wav) — that one is collected first. */
function playVoice(v) {
  if (v.sonos) return waitSonos(v);
  if (v instanceof Blob) return playBlob(v);
  if (v.mime === 'audio/mpeg') return playStream(v);
  return new Promise(function (resolve) {
    var check = function () { if (v.done) { v.notify = null; resolve(); } };
    v.notify = check;
    check();
  }).then(function () { return playBlob(new Blob(v.chunks, { type: v.mime })); });
}

/* The speaker plays on its own; the HUD only keeps "speaking" until it is done, so the mic
   does not open on top of the answer. Stop ends the wait, not the announcement. */
function waitSonos(v) {
  return new Promise(function (resolve) {
    var t = setTimeout(done, Math.max(0, v.until - performance.now()));
    function done() { clearTimeout(t); stopSpeaking = null; resolve(); }
    stopSpeaking = done;
  });
}

/* rescue (optional): hands back the whole clip as a Blob, for the AudioContext path above */
function playUrl(url, setup, onFinish, rescue) {
  return new Promise(function (resolve) {
    var finished = false;
    var done = function () {
      if (finished) return;
      finished = true;
      stopSpeaking = null;
      URL.revokeObjectURL(url);
      if (onFinish) onFinish();
      resolve();
    };
    var viaCtx = function () {
      var ctx = liveOutCtx();
      if (!rescue || !ctx) return false;
      stopSpeaking = done;     // while the rest of a stream arrives
      rescue().then(function (blob) {
        if (finished) return;
        return playOnCtx(blob, ctx, function (stop) { stopSpeaking = function () { stop(); done(); }; });
      }).catch(function (e) { log('note', 'VOICE', 'Wiedergabe fehlgeschlagen: ' + String(e && e.message || e).slice(0, 60)); })
        .then(done);
      return true;
    };
    if (Sound.blocked && viaCtx()) return;
    var audio = new Audio(url);
    audio.volume = outVolume();
    currentAudio = audio;
    if (setup) setup(function () { return finished; });
    audio.onended = done;
    audio.onerror = done;
    stopSpeaking = function () { audio.pause(); done(); };
    driveOrb(audio, function () { return finished; });
    var go = function () {
      audio.play().catch(function (e) {
        // Autoplay blocked (no click yet on this page): play on the first gesture
        // instead of silently dropping the startup briefing.
        if (e && e.name === 'NotAllowedError') {
          audio.onended = audio.onerror = null;
          if (!finished && liveOutCtx() && rescue) {
            if (!Sound.blocked) log('note', 'VOICE', 'Ton über das Mikrofon-Audio — kein Tippen nötig');
            Sound.blocked = true;
            audio.removeAttribute('src');
            if (viaCtx()) return;
          }
          audio.onended = audio.onerror = done;
          log('note', 'VOICE', 'Ton blockiert — einmal klicken, dann spricht er');
          afterGesture(function () { if (!finished) audio.play().catch(done); });
        } else done();
      });
    };
    go();
  });
}

/* ── sentence streaming ──────────────────────
   JARVIS starts talking as soon as his first sentence is written instead of
   after the whole answer: every finished sentence is sent to /api/speak right
   away (requests overlap with Claude still writing), and the clips play back
   in order. */
var Speech = { queue: [], upTo: 0, active: false, cancelled: false, waiters: [], chain: Promise.resolve() };
var MIN_CHUNK = 28;   // don't vocalise tiny fragments on their own

function speechReset() {
  Speech.queue = []; Speech.upTo = 0; Speech.cancelled = false; Speech.chain = Promise.resolve();
  Live.echo = 0; Live.echoSince = 0;
}

/* Asks for the clip at once and resolves when the first bytes are there: with
   MediaSource a stream ({mime, chunks, done, notify, cancel}) the player can start
   on while the rest arrives, otherwise a finished blob. Null = no server voice. */
var CAN_STREAM = !!(window.MediaSource && MediaSource.isTypeSupported && MediaSource.isTypeSupported('audio/mpeg'));
function fetchVoice(text) {
  if (!window.__serverVoice) return Promise.resolve(null);
  return fetch('/api/speak', {
    method: 'POST', headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ text: text, stream: CAN_STREAM })
  }).then(function (res) {
    if (!res.ok) return null;
    // a Sonos speaks for this device: the server answers with how long, not with sound
    if ((res.headers.get('content-type') || '').indexOf('application/json') === 0) {
      // "seconds" counts from this answer: the wait for the sentence may start later
      return res.json().then(function (j) {
        if (!j || !j.sonos) return null;
        return { sonos: j.sonos, held: !!j.held, until: performance.now() + (Number(j.seconds) || 0) * 1000 };
      });
    }
    if (!CAN_STREAM || !res.body) return res.blob();
    var reader = res.body.getReader();
    var vs = { mime: (res.headers.get('content-type') || '').split(';')[0].trim(), chunks: [], done: false, notify: null,
               cancel: function () { try { reader.cancel(); } catch (e) { /* gone */ } } };
    var tell = function () { if (vs.notify) vs.notify(); };
    (function pull() {
      reader.read().then(function (r) {
        if (r.done) vs.done = true; else vs.chunks.push(r.value);
        tell();
        if (!r.done) pull();
      }, function () { vs.done = true; tell(); });
    })();
    return vs;
  }).catch(function () { return null; });
}

function speakBrowser(text) {
  if (!window.speechSynthesis) return Promise.resolve();
  return new Promise(function (resolve) {
    var u = new SpeechSynthesisUtterance(stripTones(text));
    u.rate = 1.02; u.pitch = 0.92; u.volume = outVolume(); u.lang = 'de-DE';
    u.onend = resolve; u.onerror = resolve;
    stopSpeaking = function () { speechSynthesis.cancel(); resolve(); };
    speechSynthesis.speak(u);
  });
}

/* Cut finished sentences off the growing answer and queue them. */
function feedSpeech(full, final) {
  if (Speech.cancelled) return;
  var rest = full.slice(Speech.upTo);
  var re = /[\s\S]*?[.!?…:](?=\s|$)/g, m, take = 0, chunk = '';
  while ((m = re.exec(rest)) !== null && m[0].length) {
    chunk = rest.slice(take, m.index + m[0].length);
    if (stripTones(chunk).length >= MIN_CHUNK) {
      queueSpeech(chunk);
      take = m.index + m[0].length;
    }
  }
  if (final && stripTones(rest.slice(take)).trim()) {
    queueSpeech(rest.slice(take));
    take = rest.length;
  }
  Speech.upTo += take;
}

function queueSpeech(text) {
  text = text.trim();
  if (!stripTones(text)) return;
  Speech.queue.push({ text: text, blob: askVoice(text) });
  playSpeechQueue();
}

/* A Sonos plays one stream that every sentence is appended to (sonos.py): they must arrive in order,
   so each asks once the one before is in. The device's own clips may overlap. */
function askVoice(text) {
  if (!Output.target) return fetchVoice(text);
  var blob = Speech.chain.then(function () { return fetchVoice(text); });
  Speech.chain = blob.catch(function () {});
  return blob;
}

/* ── talking over him ─────────────────────────
   He pauses as soon as someone talks over him for a moment (Barge), and goes on with the sentence he
   was in when nothing came of it — silence, a cough, an "mhm", his own voice from the speaker. Words
   are the next request. On a Sonos the announcement stops and further sentences wait (sonos.stop). */
function sonosHalt(hold) {
  if (!Output.target) return Promise.resolve();
  return fetch('/api/speak/stop', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ hold: !!hold }) }).catch(function () {});
}

function pauseSpeech() {
  if (!Speech.active || Speech.paused || Speech.cancelled) return false;
  Speech.paused = true;
  Speech.pausedAt = performance.now();
  sonosHalt(true);
  if (stopSpeaking) { var stop = stopSpeaking; stopSpeaking = null; stop(); }
  log('note', 'VOICE', 'Pause — ich höre');
  var at = Speech.pausedAt;   // a clip that never comes back (recorder broke) must not keep him silent
  setTimeout(function () {
    if (Speech.paused && Speech.pausedAt === at && !Barge.rec && !Barge.busy) resumeSpeech();
  }, BARGE_MAX_PAUSED_MS + 10000);
  return true;
}

function resumeSpeech() {
  if (!Speech.paused) return;
  Speech.paused = false;
  // the Sonos threw away what it had; here only the interrupted clip is gone
  if (Output.target) Speech.chain = Speech.chain.then(function () { return sonosHalt(false); });
  Speech.queue.forEach(function (it) {
    if (Output.target || it.again) { it.blob = askVoice(it.text); it.again = false; }
  });
  log('note', 'VOICE', 'weiter');
  var go = Speech.onResume; Speech.onResume = null;
  if (go) go();
}

async function playSpeechQueue() {
  if (Speech.active) return;
  Speech.active = true;
  setVoiceState('SPEAKING', 'hot');
  Live.speaking = true;
  duckMusic(true);
  try {
    while (Speech.queue.length && !Speech.cancelled) {
      var item = Speech.queue.shift();
      var voice = await item.blob;
      if (Speech.cancelled) { if (voice && voice.cancel) voice.cancel(); break; }
      if (!Speech.paused && !(voice && voice.held)) {
        if (voice) await playVoice(voice);
        else await speakBrowser(item.text);
      }
      if (Speech.paused || (voice && voice.held)) {
        // cut off (or kept back on the Sonos): this sentence again from its start once he goes on
        item.again = true;
        Speech.queue.unshift(item);
        if (Speech.paused) await new Promise(function (resolve) { Speech.onResume = resolve; });
        else { await new Promise(function (r) { setTimeout(r, 300); }); item.blob = askVoice(item.text); }
      }
    }
  } finally {
    Speech.active = false;
    stopSpeaking = null;
    Live.speaking = false;
    Live.spokeAt = performance.now();
    duckMusic(false);
    if (!Speech.cancelled) Wake.followUp(answerText);   // "Und morgen?" needs no wake word
    if (!Live.armed && !running) setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
    var w = Speech.waiters; Speech.waiters = [];
    w.forEach(function (f) { f(); });
  }
}

/* Resolves once everything queued has been spoken (or cut off). */
function speechDrained() {
  if (!Speech.active && !Speech.queue.length) return Promise.resolve();
  return new Promise(function (resolve) { Speech.waiters.push(resolve); });
}

/* Resolves when playback actually FINISHES, not when it starts — live mode
   needs that to know when it is safe to listen again. */
async function speak(text) {
  if (typeof VoiceClient !== 'undefined' && VoiceClient.on && VoiceClient.ready) return VoiceClient.say(text);
  setVoiceState('SPEAKING', 'hot');
  Live.speaking = true;
  duckMusic(true);
  try {
    if (window.__serverVoice) {
      var v = await fetchVoice(text);
      if (v) { await playVoice(v); return; }
      log('note', 'VOICE', 'server voice failed, using browser');
    }
    if (!window.speechSynthesis) return;
    await new Promise(function (resolve) {
      var u = new SpeechSynthesisUtterance(stripTones(text));
      u.rate = 1.02; u.pitch = 0.92;
      u.volume = outVolume();
      u.lang = 'de-DE';
      u.onend = resolve; u.onerror = resolve;
      stopSpeaking = function () { speechSynthesis.cancel(); resolve(); };
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    });
  } finally {
    stopSpeaking = null;
    Live.speaking = false;
    Live.spokeAt = performance.now();
    duckMusic(false);
    Wake.followUp(text);
    if (!Live.armed) setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
  }
}

/* ── where this device's answers sound ───────
   Per device, kept by the server (sonos.py): the iPad itself, or a Sonos in the house. The button
   steps through them; it stays hidden while no speaker is set up (JARVIS_SONOS). */
var Output = {
  target: '', label: '', speakers: [],
  update: function (o) {
    if (!o) return;
    Output.speakers = o.speakers || [];
    Output.target = o.output || '';
    Output.label = o.output_label || '';
    Output.render();
  },
  render: function () {
    var btn = $('#btnOutput');
    if (!btn) return;
    btn.hidden = !Output.speakers.length;
    btn.textContent = Output.target ? '🔊 ' + Output.label : '🔈 Ton hier';
    btn.classList.toggle('on', !!Output.target);
  },
  next: function () {
    var ids = [''].concat(Output.speakers.map(function (sp) { return sp.id; }));
    var target = ids[(ids.indexOf(Output.target) + 1) % ids.length];
    fetch('/api/output', {
      method: 'POST', headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ target: target })
    }).then(function (res) { return res.ok ? res.json() : null; }).then(function (j) {
      if (!j) return log('note', 'VOICE', 'Ausgabe nicht umgestellt');
      Output.target = j.output || ''; Output.label = j.output_label || '';
      Output.render();
      log('note', 'VOICE', Output.target ? 'Antworten dieses Geräts kommen aus ' + Output.label : 'Antworten kommen wieder aus diesem Gerät');
    }).catch(function () { log('note', 'VOICE', 'Ausgabe nicht umgestellt'); });
  },
  init: function () {
    var btn = $('#btnOutput');
    if (btn) btn.onclick = Output.next;
    Output.render();
  }
};

/* ── sound in the room ───────────────────────
   Music, a match or the TV talk all the time, and every "Jarvis"-like sound in them used to
   cost a transcription or worse. Two sources: the house (ambience.py — Home Assistant says a
   player is playing) and the mic itself — a level above the quiet room almost without a pause
   for ~10 s is not a person, people breathe. With either, the wake word gets stricter
   (OnDevice.need, Wake.check) and the screen says that "Hey Jarvis" is the safe way. */
var AMB_RATE = 0.0015;     // per mic frame (~16 ms): ~10 s to fill
var AMB_ON = 0.85, AMB_OFF = 0.5;   // share of loud frames — with a gap so the hint does not flicker
var AMB_LABEL = { sport: 'Sport läuft', tv: 'Fernseher läuft', music: 'Musik läuft', noise: 'Laut im Raum' };
var Ambience = {
  fromHouse: '', busy: 0, ear: false,
  house: function (a) {
    var k = (a && a.kind) || '';
    if (k === Ambience.fromHouse) return;
    Ambience.fromHouse = k;
    if (k) log('note', 'WAKE', AMB_LABEL[k] + ' (' + ((a.players || []).join(', ') || 'Home Assistant') + ') — „Jarvis“ zählt nur am Satzanfang oder -ende');
    updateMicButton();
  },
  hear: function (loud) {
    Ambience.busy += ((loud ? 1 : 0) - Ambience.busy) * AMB_RATE;
    var was = Ambience.ear;
    if (!was && Ambience.busy > AMB_ON) Ambience.ear = true;
    else if (was && Ambience.busy < AMB_OFF) Ambience.ear = false;
    if (Ambience.ear !== was) {
      if (Ambience.ear && !Ambience.fromHouse) log('note', 'WAKE', 'Dauergeräusch im Raum — strengere Erkennung');
      WakeLog.send(Ambience.ear ? 'Dauergeräusch erkannt' : 'Raum wieder ruhig');
      updateMicButton();
    }
  },
  kind: function () { return Ambience.fromHouse || (Ambience.ear ? 'noise' : ''); },
  strict: function () { return Wake.mode() !== 'off' && !!Ambience.kind(); },
  hint: function () { var k = Ambience.kind(); return k ? AMB_LABEL[k] + ' · sag „Hey Jarvis“' : 'Sag „Jarvis“'; }
};

/* ── wake word ─────────────────────────────────
   JARVIS_WAKE on the server: off = every utterance counts (as before);
   text = only utterances addressed to "Jarvis" — at the start ("Jarvis, mach
   das Licht an") or at the end ("Mach das Licht an, Jarvis"); oww = the
   server also hears "Hey Jarvis" (openWakeWord) and opens the mic for a few
   seconds. After every answer there is a follow-up window without the wake word. */
// Spellings Whisper produces for "Jarvis"; "arvis" is one whose first sound got lost.
var WAKE_NAME = '(?:jarvis|jarviz|jarves|javis|jarwis|jervis|yarvis|garvis|tscharvis|tschawis|dschawis|dschavis|'
  + 'charvis|chavis|arvis|jarvi)';
var WAKE_RE = new RegExp('^\\W*(?:(?:hey|hi|hallo|ok|okay|he|ey|du)\\W+)?' + WAKE_NAME + '\\b[\\s,.!?:;-]*', 'i');
var WAKE_END_RE = new RegExp('[\\s,;-]+(?:bitte\\W+)?' + WAKE_NAME + '\\W*$', 'i');
// "Kannst du, Jarvis, mal ..." — the name in the middle counts too
var WAKE_MID_RE = new RegExp('(^|[\\s,.!?;:-]+)' + WAKE_NAME + '(?![\\p{L}])[\\s,.!?:;-]*', 'iu');
var Wake = {
  cfg: { mode: 'off', window_s: 7, followup_s: 0 },
  until: 0,
  configure: function (s) { Wake.cfg = Object.assign(Wake.cfg, s || {}); },
  mode: function () { return Wake.cfg.mode || 'off'; },
  awake: function () {
    return Wake.mode() === 'off' || !!pendingConfirm || performance.now() < Wake.until;
  },
  open: function (seconds) {
    Wake.until = Math.max(Wake.until, performance.now() + seconds * 1000);
    updateMicButton();
    setTimeout(updateMicButton, seconds * 1000 + 50);
  },
  /* after Jarvis spoke: a moment to answer him without the wake word — only if
     JARVIS_FOLLOWUP_S is set; by default every request starts with "Jarvis" */
  followUp: function (text) {
    var s = Number(Wake.cfg.followup_s) || 0;
    if (Wake.mode() === 'off' || s <= 0) return;
    var asked = /\?\s*$/.test(stripTones(text || ''));
    Wake.open(s * (asked ? 1.6 : 1));
  },
  /* the server heard "Hey Jarvis" */
  lastHeard: -1e9,
  heard: function () {
    Wake.lastHeard = performance.now();
    if (!Live.on) return;
    if (typeof VoiceClient !== 'undefined' && VoiceClient.on) { VoiceClient.wakeHeard(); wakeChime(); return; }
    if (Live.speaking || Speech.active || Ringing.on) { interruptSpeech(); Ringing.stop(); }
    MusicHold.hold();
    Wake.open(Wake.cfg.window_s || 7);
    wakeChime();
    log('note', 'WAKE', 'Aktivierungswort gehört');
  },
  /* text mode: strip the wake word, or say the utterance was not meant for us */
  /* strict (music, sport, TV in the room): only "Jarvis" at the start or end of the sentence
     counts — a name in the middle of a commentator's line is not a request */
  check: function (text, startedAwake, strict) {
    var m = WAKE_RE.exec(text);
    if (m) return { ok: true, text: text.slice(m[0].length).trim() };
    var e = WAKE_END_RE.exec(text);
    // keep the full stop / question mark, or the sentence sounds unfinished and is held back
    if (e) return { ok: true, text: text.slice(0, e.index).trim() + ((/[.!?]\s*$/.exec(text) || [''])[0].trim()) };
    var mid = !strict && WAKE_MID_RE.exec(text);
    if (mid) return { ok: true, text: (text.slice(0, mid.index) + ' ' + text.slice(mid.index + mid[0].length)).replace(/\s+/g, ' ').trim() };
    if (startedAwake) return { ok: true, text: text };
    return { ok: false, text: text };
  }
};

function wakeChime() {
  try {
    var bc = chimeCtx();
    [880, 1320].forEach(function (f, i) {
      var t = bc.currentTime + 0.02 + i * 0.09;
      var osc = bc.createOscillator(), gain = bc.createGain();
      osc.type = 'sine'; osc.frequency.value = f;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.06 * voiceVolume + 0.01, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
      osc.connect(gain); gain.connect(bc.destination);
      osc.start(t); osc.stop(t + 0.13);
    });
  } catch (e) { /* no audio, no chime */ }
}

/* ── alarm ringing ─────────────────────────────
   Timers, the alarm clock and reminders ring before Jarvis speaks. Talking,
   Esc, Space or the Stop button end the ringing (it counts as "speaking", so
   the normal barge-in path stops it). */
var Ringing = {
  on: false, timer: null, done: null,
  interrupted: false,
  start: function (seconds) {
    return new Promise(function (resolve) {
      Ringing.on = true;
      Ringing.interrupted = false;
      Ringing.done = resolve;
      Live.speaking = true;
      stopSpeaking = function () { Ringing.interrupted = true; Ringing.stop(); };
      setVoiceState('KLINGELT', 'hot');
      var end = performance.now() + seconds * 1000;
      (function pulse() {
        if (!Ringing.on) return;
        if (performance.now() > end) return Ringing.stop();
        try {
          var bc = chimeCtx();
          [0, 0.18, 0.36].forEach(function (off) {
            var t = bc.currentTime + 0.02 + off;
            var osc = bc.createOscillator(), gain = bc.createGain();
            osc.type = 'triangle'; osc.frequency.value = 1046;
            gain.gain.setValueAtTime(0.0001, t);
            gain.gain.exponentialRampToValueAtTime(0.18 * Math.max(voiceVolume, 0.3), t + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
            osc.connect(gain); gain.connect(bc.destination);
            osc.start(t); osc.stop(t + 0.15);
          });
        } catch (e) { /* no audio — the text still appears */ }
        Ringing.timer = setTimeout(pulse, 1300);
      })();
    });
  },
  stop: function () {
    if (!Ringing.on) return;
    Ringing.on = false;
    clearTimeout(Ringing.timer);
    if (stopSpeaking && !Speech.active) stopSpeaking = null;
    Live.speaking = false;
    Live.spokeAt = performance.now();
    var d = Ringing.done; Ringing.done = null;
    if (d) d();
    updateMicButton();
  }
};

/* ── one voice per browser ─────────────────────
   Every HUD tab used to open the microphone, hear the wake word and read out every notice,
   so two or three tabs (autostart plus a manual start, a window left over) answered the same
   question in chorus. Now the tab holding the Web Lock "jarvis-voice" listens and speaks; the
   others only show. When that tab closes, the lock passes to the next one, which takes over.
   Clicking the mic in a silent tab takes the voice over on purpose. Browsers without Web Locks
   keep the old behaviour (every tab speaks). */
var VoiceOwner = {
  me: false, ceded: false, decided: null,

  speaks: function () { return VoiceOwner.me && !VoiceOwner.ceded; },

  decide: function () {
    if (VoiceOwner.decided) return VoiceOwner.decided;
    VoiceOwner.decided = new Promise(function (resolve) {
      if (!(navigator.locks && navigator.locks.request)) { VoiceOwner.me = true; resolve(true); return; }
      navigator.locks.request('jarvis-voice', { ifAvailable: true }, function (lock) {
        if (lock) { VoiceOwner.me = true; resolve(true); return new Promise(function () {}); }   // held while the tab lives
        VoiceOwner.silent('ein anderer Jarvis-Tab hat die Stimme — dieser zeigt nur an');
        resolve(false);
        navigator.locks.request('jarvis-voice', function () {          // queued: ours once that tab closes
          VoiceOwner.me = true;
          VoiceOwner.promoted();
          return new Promise(function () {});
        });
      }).catch(function () { VoiceOwner.me = true; resolve(true); });
    });
    return VoiceOwner.decided;
  },

  silent: function (why) {
    document.body.classList.add('passive');
    log('note', 'STIMME', why);
  },

  promoted: function () {
    if (VoiceOwner.ceded) return;
    document.body.classList.remove('passive');
    log('note', 'STIMME', 'der andere Jarvis-Tab ist zu — dieser hört und spricht jetzt');
    if (window.__serverSTT && localStorage.getItem('jarvis.live') !== '0' && !Live.on) Live.enable();
  },

  /* the voice engine gave our session to a newer window (another address of the same PC) */
  lost: function () {
    VoiceOwner.ceded = true;
    VoiceOwner.silent('ein anderes Jarvis-Fenster hat jetzt die Stimme — dieses zeigt nur an');
  },

  /* mic clicked in a silent tab: the user wants this one */
  take: function () {
    if (VoiceOwner.speaks()) return;
    VoiceOwner.me = true; VoiceOwner.ceded = false;
    document.body.classList.remove('passive');
    log('note', 'STIMME', 'dieser Tab hat jetzt die Stimme');
  }
};

/* ── push from the server (/api/events) ────────
   Timers ringing, "Die Waschmaschine ist fertig", appointments, new devices,
   the wake word. Spoken only when nothing else is going on; during the quiet
   hours notices are only shown (alarms still ring). */
var Push = { queue: [], busy: false, es: null };

function connectEvents() {
  try {
    Push.es = new EventSource('/api/events?token=' + encodeURIComponent(TOKEN));
  } catch (e) { return; }
  // A stream the browser gave up on (server gone, 502 from Tailscale) never comes back by itself.
  Push.es.onerror = function () { if (Push.es.readyState === 2) Reconnect.lost(); };
  Push.es.onmessage = function (m) {
    var ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    // The wake word is heard by the PC's microphone — a phone elsewhere must not start listening.
    if (ev.t === 'wake') return (window.__remote || !VoiceOwner.speaks()) ? undefined : Wake.heard();
    if (ev.t === 'timers') return Timers.set(ev.items || []);
    // Stop / Mic on the desktop card (overlay.py). Only fresh ones: a reconnect replays recent events.
    if (ev.t === 'hud') {
      if (window.__remote || !VoiceOwner.speaks() || Date.now() / 1000 - (ev.at || 0) > 5) return;
      if (ev.cmd === 'stop') { var sb = $('#btnStop'); if (sb && !sb.disabled) sb.click(); }
      else if (ev.cmd === 'mic') toggleLive();
      return;
    }
    if (ev.t === 'task') { log('note', 'AUFGABE', ev.title + ' · ' + ev.status + ' · ' + ev.done + '/' + ev.total); return; }
    if (ev.t === 'say' && window.__remote) ev = Object.assign({}, ev, { silent: true });   // the PC reads it out
    if (ev.t === 'say' || ev.t === 'alarm') { Push.queue.push(ev); drainPush(); Notes.bump(); }
  };
}

function pushBlocked() {
  return running || Live.armed || Live.sending || Live.speaking || Speech.active || !!pendingConfirm;
}

async function drainPush() {
  if (Push.busy) return;
  Push.busy = true;
  try {
    await VoiceOwner.decide();          // events can arrive before the boot decided who speaks
    while (Push.queue.length) {
      if (pushBlocked()) { await new Promise(function (r) { setTimeout(r, 800); }); continue; }
      var ev = Push.queue.shift();
      var b = bubble('jarvis', ev.text || '');
      b.classList.add('push');
      log(ev.t === 'alarm' ? 'run' : 'note', ev.t === 'alarm' ? 'WECKER' : 'MELDUNG', ev.text || '');
      if (!VoiceOwner.speaks()) {
        b.classList.add('silent');      // the speaking tab says (or rings) it
      } else if (ev.t === 'alarm') {
        await Ringing.start(ev.kind === 'alarm' ? 45 : 8);
        // Talked over the ringing ("Stopp", "Schlummern"): he listens instead of reading it out.
        if (ev.text && !Ringing.interrupted) await speak(ev.text);
        else Wake.followUp('?');
      } else if (ev.silent) {
        b.classList.add('silent');
      } else if (ev.text) {
        wakeChime();
        await speak(ev.text);
      }
    }
  } finally { Push.busy = false; }
}

/* Countdown chip in the top bar: the next timer, alarm or reminder. */
var Timers = {
  items: [],
  set: function (items) { Timers.items = (items || []).slice().sort(function (a, b) { return a.at - b.at; }); Timers.render(); },
  render: function () {
    var chip = $('#chipTimer');
    if (!chip) return;
    var now = Date.now() / 1000, list = Timers.items.filter(function (i) { return i.at > now - 2; });
    if (!list.length) { chip.hidden = true; return; }
    chip.hidden = false;
    var next = list[0], left = Math.max(0, Math.round(next.at - now)), txt;
    if (next.kind === 'timer' || left < 3600) {
      var h = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = left % 60;
      txt = (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
    } else {
      var d = new Date(next.at * 1000);
      txt = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
      if (d.toDateString() !== new Date().toDateString()) txt = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'][d.getDay()] + ' ' + txt;
    }
    var label = { timer: 'Timer', alarm: 'Wecker', reminder: 'Erinnerung', action: 'Geplant' }[next.kind] || '';
    $('#tNext').textContent = txt;
    $('#tSub').textContent = label + (next.text ? ' · ' + next.text : '') + (list.length > 1 ? ' · +' + (list.length - 1) : '');
    $('#chipTimer .ico').textContent = next.kind === 'alarm' ? '⏰' : next.kind === 'reminder' ? '🔔' : '⏱';
    chip.title = list.map(function (i) {
      return ({ timer: 'Timer', alarm: 'Wecker', reminder: 'Erinnerung', action: 'Geplant' }[i.kind] || i.kind) + ' '
        + new Date(i.at * 1000).toLocaleString('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
        + (i.text ? ' — ' + i.text : '');
    }).join('\n');
  }
};

/* ── live voice ───────────────────────────────
   Hold the mic open, watch the input level, and cut an utterance when you stop
   talking. No button press per turn.

   Two things this has to get right:
   - It must not hear JARVIS. Detection is suspended while a turn is running and
     while audio is playing, otherwise the reply gets transcribed as your next
     question and it talks to itself forever.
   - Room tone varies. The threshold is calibrated from your actual noise floor
     at start rather than hardcoded, so a noisy room does not trigger constantly. */
var CONTINUE_MS = 7000;        // talk again this soon after sending = same sentence
var CONTINUE_SUSTAIN_MS = 250; // ...for at least this long (a cough does not count)
var SILENCE_MS = 2000;    // quiet this long ends the utterance (people pause to think — shorter cuts them off)
// After that pause the clip is transcribed; if the text sounds unfinished (ends on
// "ähm", "und", "dass", a comma, or without a full stop) it is held this long for the rest.
var UNFINISHED_HOLD_MS = 3000;
var UNFINISHED_END = /(?:^|\s)(ähm|äh|ehm|öhm|hm+|mhm|und|oder|aber|also|dass|weil|wenn|ob|der|die|den|dem|des|ein|eine|einen|einem|mit|für|von|vom|zum|zur|im|halt|quasi|sozusagen|irgendwie|nämlich|bitte\s*,)\s*[,.…-]*\s*$/i;

function looksUnfinished(text) {
  var t = String(text || '').trim();
  if (!t) return false;
  if (/(\.\.\.|…|,|-|–)\s*$/.test(t)) return true;          // trailing comma / ellipsis / dash
  if (UNFINISHED_END.test(t)) return true;                   // trailing filler or connector word
  if (!/[.!?]["”'»)]?\s*$/.test(t) && t.split(/\s+/).length >= 3) return true;   // no full stop at all
  return false;
}
var MIN_SPEECH_MS = 250;   // shorter than this is a cough, not a sentence (a quick "Jarvis" is ~300 ms)
var MAX_SPEECH_MS = 20000; // hard stop so a stuck mic cannot record forever
// Talking over JARVIS records a clip while he keeps talking; only "Jarvis" in it stops him.
var BARGE_MS = 200;        // shorter than this is a blip, not worth a transcription
var BARGE_FACTOR = 3;      // ...this many times above the quiet-room threshold (raise if his own voice triggers it)
var ECHO_TAIL_MS = 700;    // stay deaf this long after he stops talking (room echo of his own voice)
var BARGE_MIN = 0.02;      // ...and at least this loud
var BARGE_AMBIENT = 2.2;   // ...and this much louder than steady background (TV, music)
var BARGE_SILENCE_MS = 550;   // a pause this long ends the clip — "Jarvis" alone is short
var BARGE_MAX_MS = 6000;      // longer than this without a pause: check what is there
var BARGE_STOP_ONLY = /^\W*(stopp?|halt|ruhe|still|sei (mal )?still|schon gut|danke|genug|warte|moment|pause)\W*$/i;
var PAUSE_MS = 400;              // talking over him this long pauses him (shorter: a cough, an "mhm")
var BARGE_SILENCE_PAUSED_MS = 1000;   // paused, he waits a little longer for the first word
var BARGE_MAX_PAUSED_MS = 15000;      // ...and lets a whole request through
var BARGE_FILLER = /^\W*(m+h*m+|h+m+|ä+h*m*|aha|okay|ok|genau|ach so|alles klar)\W*$/i;
// On a Sonos the mic hears him as well (echo cancellation only knows the device's own speaker):
var ECHO_FACTOR = 1.8;           // a voice must be this much louder than his loudest recent moment
var ECHO_DECAY = 0.996;          // per frame (~16 ms): that moment fades over a few seconds
var ECHO_WARMUP_MS = 1200;       // his first words from the Sonos teach the level, nobody barges in before
var BRIEFING_BARGE_FACTOR = 1.6;  // while the briefing is being prepared, normal speech is enough
var PTT_HOLD_MS = 350;     // hold the Live button this long = push-to-talk
// Background music: the threshold follows a slow average of the room level, so
// steady sound (music, TV, fan) lifts it while short speech bursts barely do.
var AMBIENT_RATE = 0.002;  // per frame — ~8 s to settle at 60 fps
var AMBIENT_FACTOR = 1.8;  // speak this much louder than the background
var NOISE_TEXT = /^\W*[\[(*♪]?\s*(musik|music|applaus|lachen|gelächter|untertitel|copyright|♪)/i;

// Posts every 6 render quanta (768 samples, ~16 ms) — about the old frame rate,
// which AMBIENT_RATE is tuned for.
var TICK_WORKLET = 'registerProcessor("jarvis-tick", class extends AudioWorkletProcessor {'
  + 'constructor() { super(); this.n = 0; }'
  + 'process() { if (++this.n >= 6) { this.n = 0; this.port.postMessage(0); } return true; }'
  + '});';

/* Microphone as raw samples for the clips sent to Whisper. MediaRecorder starts only
   when the level crosses the threshold, and the soft start of a word lies before that:
   "Jarvis" arrived as "arvis" and the wake word check threw the whole sentence away.
   Here the last PREROLL_MS are always kept, and every clip starts with them (WAV, 16 kHz). */
var PREROLL_MS = 600;
var CAP_WORKLET = 'registerProcessor("jarvis-cap", class extends AudioWorkletProcessor {'
  + 'constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }'
  + 'process(inputs) { var ch = inputs[0] && inputs[0][0];'
  + ' if (ch) { this.buf.set(ch, this.n); this.n += ch.length;'
  + '  if (this.n >= 2048) { this.port.postMessage(this.buf.slice(0, this.n)); this.n = 0; } }'
  + ' return true; }'
  + '});';

var Cap = {
  ok: false, rate: 48000, ring: [], ringLen: 0, takes: [], node: null,
  async start(ctx, source) {
    try {
      var url = URL.createObjectURL(new Blob([CAP_WORKLET], { type: 'application/javascript' }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      var node = new AudioWorkletNode(ctx, 'jarvis-cap', { numberOfInputs: 1, numberOfOutputs: 1,
                                                           channelCount: 1, channelCountMode: 'explicit' });
      var mute = ctx.createGain();
      mute.gain.value = 0;
      source.connect(node);
      node.connect(mute).connect(ctx.destination);     // an unconnected node is never processed
      node.port.onmessage = function (e) { Cap.feed(e.data); };
      Cap.rate = ctx.sampleRate; Cap.node = node; Cap.ok = true;
    } catch (e) {
      Cap.ok = false;                                   // MediaRecorder stays as the fallback
    }
  },
  stop() {
    if (Cap.node) { Cap.node.port.onmessage = null; try { Cap.node.disconnect(); } catch (e) { /* gone */ } }
    Cap.node = null; Cap.ok = false; Cap.ring = []; Cap.ringLen = 0; Cap.takes = [];
  },
  feed(block) {
    if (OnDevice.on) OnDevice.push(block);
    Cap.ring.push(block);
    Cap.ringLen += block.length;
    var keep = Cap.rate * PREROLL_MS / 1000;
    while (Cap.ring.length > 1 && Cap.ringLen - Cap.ring[0].length >= keep) Cap.ringLen -= Cap.ring.shift().length;
    for (var i = 0; i < Cap.takes.length; i++) Cap.takes[i].blocks.push(block);
  },
  /* a clip that already holds the pre-roll; end() hands back the WAV (or drops it) */
  begin() {
    var t = { blocks: Cap.ring.slice() };
    Cap.takes.push(t);
    return t;
  },
  end(t, keep) {
    Cap.takes = Cap.takes.filter(function (x) { return x !== t; });
    return keep ? Cap.wav(t.blocks) : null;
  },
  wav(blocks) {
    var n = 0, i, j;
    for (i = 0; i < blocks.length; i++) n += blocks[i].length;
    var all = new Float32Array(n), off = 0;
    for (i = 0; i < blocks.length; i++) { all.set(blocks[i], off); off += blocks[i].length; }
    // 16 kHz mono is what Whisper wants; averaging over each step keeps it from aliasing
    var step = Cap.rate / 16000, len = Math.floor(n / step), pcm = new Int16Array(len);
    for (i = 0; i < len; i++) {
      var a = Math.floor(i * step), b = Math.min(n, Math.floor((i + 1) * step)), sum = 0;
      for (j = a; j < b; j++) sum += all[j];
      var v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    var buf = new ArrayBuffer(44 + pcm.length * 2), d = new DataView(buf);
    var str = function (o, t) { for (var k = 0; k < t.length; k++) d.setUint8(o + k, t.charCodeAt(k)); };
    str(0, 'RIFF'); d.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
    d.setUint32(16, 16, true); d.setUint16(20, 1, true); d.setUint16(22, 1, true); d.setUint32(24, 16000, true);
    d.setUint32(28, 32000, true); d.setUint16(32, 2, true); d.setUint16(34, 16, true); str(36, 'data');
    d.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: 'audio/wav' });
  }
};

/* What the wake word on a phone or iPad did, in logs/wake_device.log on the PC: the HUD log
   on the iPad is out of reach when "Jarvis" is not heard. Scores and lengths only, no words. */
var WakeLog = {
  send: function (text, always) {
    if (!window.__remote && !always) return;
    fetch('/api/wakelog', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ text: text }) }).catch(function () {});
  }
};

/* Wake word on the device (iPad, iPhone): openWakeWord runs in a worker (wakeword.js) on
   the microphone signal, and a clip only goes to the PC once it heard something like
   "Jarvis" — before, every sound in the room was sent and transcribed there. A pre-filter
   on purpose: the model knows the English "Hey Jarvis", a German "Jarvis" scores far lower,
   so the bar is low and the PC still checks the words. Without the model files
   (scripts/fetch_wakeword.py) or on the PC itself everything stays as it was. */
var OnDevice = {
  worker: null, ready: false, on: false, failed: false,
  rate: 48000, phase: 0, acc: 0, cnt: 0, out: null, n: 0, scores: [], firedAt: -1e9, faintAt: -1e9,
  SURE: 0.5,   // "Hey Jarvis" scores ~0.99, a clear English "Jarvis" ~0.97; a German one ~0.03-0.1

  threshold: function () { return Number(Wake.cfg.local_threshold) || 0.02; },
  models: function () { return (Wake.cfg.local_models || []).length ? Wake.cfg.local_models : ['hey_jarvis']; },
  /* What a clip needs to go to the PC. The model is trained on "Hey Jarvis"; a German "Jarvis",
     alone or in front of a request, scored anywhere from 0.005 to 0.07 on the iPad (one sentence
     took three tries at 0.008, 0.010, 0.042), while room sounds stay at 0.000. So a clip of
     normal request length only has to clear that floor and the PC's transcript decides; long
     talk (TV, a phone call) keeps the real bar. With music or sport in the room the floor is
     off, but the bar stays: doubling it let no German "Jarvis" through at all.
   These numbers are for "hey_jarvis"; JARVIS_WAKE_LOCAL_MODEL picks another deciding model
   (jarvis_v1, jarvis_v2: trained on a plain "Jarvis"), the others run alongside for the log. */
  SHORT_FLOOR: 0.004,
  REQUEST_MS: 8000,
  need: function (durMs) {
    var t = OnDevice.threshold();
    return !Ambience.strict() && durMs > 0 && durMs <= OnDevice.REQUEST_MS ? Math.min(t, OnDevice.SHORT_FLOOR) : t;
  },
  /* From here the device's ear alone decides, whatever Whisper spells: it wrote "Jarvis" as
     something else at 0.07 and 0.34. A short request needs less certainty than a long clip. */
  trusted: function (peak, durMs) { return peak >= (durMs > 0 && durMs <= 6000 ? 0.06 : 0.2); },
  /* true = a clip without a detection is not sent at all */
  gate: function () { return OnDevice.ready && OnDevice.on && Wake.mode() !== 'off'; },

  start: function (rate) {
    if (!window.__remote || OnDevice.failed || typeof Worker === 'undefined') return;
    OnDevice.rate = rate; OnDevice.phase = 0; OnDevice.acc = 0; OnDevice.cnt = 0;
    OnDevice.out = new Float32Array(1280); OnDevice.n = 0;
    OnDevice.on = true;
    if (OnDevice.worker) { OnDevice.worker.postMessage({ t: 'reset' }); return; }
    try { OnDevice.worker = new Worker('/wakeword.js?m=' + encodeURIComponent(OnDevice.models().join(','))); }
    catch (e) { OnDevice.failed = true; OnDevice.on = false; return; }
    OnDevice.worker.onmessage = function (ev) {
      var d = ev.data;
      if (d.t === 'ready') {
        OnDevice.ready = true;
        log('ok', 'WAKE', 'Aktivierungswort hört jetzt auf diesem Gerät — ohne „Jarvis“ geht nichts an den PC');
        var ms = d.models || OnDevice.models();
        WakeLog.send('Erkennung auf dem Gerät bereit · Modell ' + ms[0]
          + (ms.length > 1 ? ' (zum Vergleich: ' + ms.slice(1).join(', ') + ')' : '') + ' · Schwelle ' + OnDevice.threshold());
      } else if (d.t === 'error') {
        OnDevice.failed = true; OnDevice.ready = false; OnDevice.on = false;
        log('note', 'WAKE', 'Erkennung auf dem Gerät nicht verfügbar (' + d.msg + ') — der PC prüft wie bisher');
        WakeLog.send('Erkennung auf dem Gerät fehlgeschlagen: ' + d.msg);
      } else if (d.t === 'score') {
        var top = d.s;
        for (var k in d.by || {}) top = Math.max(top, d.by[k]);
        if (top >= 0.005) {
          OnDevice.scores.push({ s: d.s, by: d.by || {}, at: d.at });
          if (OnDevice.scores.length > 200) OnDevice.scores.shift();
        }
        // A sure hit ("Hey Jarvis") opens the mic like the PC's wake word. A faint one — a
        // German "Jarvis", or music and talk that sound a bit like it — only lets the clip
        // go to the PC, where the words must name him: before, a faint hit from the radio
        // opened the window and the next lyric line became a request.
        if (d.s >= OnDevice.SURE && d.at - OnDevice.firedAt > 1500) {
          OnDevice.firedAt = d.at;
          Wake.heard();
        } else if (d.s >= OnDevice.need(0)) {
          OnDevice.faintAt = d.at;
        }
      }
    };
  },
  stop: function () { OnDevice.on = false; },

  /* the capture blocks (any rate, -1..1) as 16 kHz in int16 range, 80 ms per message */
  push: function (block) {
    var rate = OnDevice.rate, out = OnDevice.out;
    for (var i = 0; i < block.length; i++) {
      OnDevice.acc += block[i]; OnDevice.cnt++;
      OnDevice.phase += 16000;
      if (OnDevice.phase < rate) continue;
      OnDevice.phase -= rate;
      out[OnDevice.n++] = Math.max(-1, Math.min(1, OnDevice.acc / OnDevice.cnt)) * 32767;
      OnDevice.acc = 0; OnDevice.cnt = 0;
      if (OnDevice.n === out.length) {
        if (OnDevice.ready) OnDevice.worker.postMessage({ t: 'pcm', pcm: out, at: performance.now() }, [out.buffer]);
        OnDevice.out = out = new Float32Array(1280); OnDevice.n = 0;
      }
    }
  },

  /* the highest score since `since` — decides the gate, and the log helps to set JARVIS_WAKE_LOCAL_THRESHOLD */
  peak: function (since) {
    var b = 0;
    for (var i = 0; i < OnDevice.scores.length; i++) if (OnDevice.scores[i].at >= since) b = Math.max(b, OnDevice.scores[i].s);
    return b;
  },
  /* every model's highest score since `since`, for the log: "hey_jarvis 0.012 · jarvis_v2 0.310" */
  peaks: function (since) {
    var ms = OnDevice.models(), best = {};
    if (ms.length < 2) return '';
    for (var i = 0; i < OnDevice.scores.length; i++) {
      var sc = OnDevice.scores[i];
      if (sc.at < since) continue;
      for (var k in sc.by) best[k] = Math.max(best[k] || 0, sc.by[k]);
    }
    return ms.map(function (m) { return m + ' ' + (best[m] || 0).toFixed(3); }).join(' · ');
  }
};

var Live = {
  on: false, speaking: false, armed: false, sending: false,
  stream: null, ctx: null, analyser: null, data: null,
  recorder: null, chunks: [], raf: 0,
  threshold: 0.02, base: 0.02, ambient: 0, voiceStart: 0, lastVoice: 0, bargeSince: 0,
  lastSent: null, continuing: false, contSince: 0,

  async enable() {
    if (Live.on) return;
    if (!window.__serverSTT) await loadStatus();   // status may simply not be loaded yet
    if (typeof VoiceClient !== 'undefined' && VoiceClient.wanted()) {
      if (await VoiceClient.start()) { updateMicButton(); return; }
    }
    if (!window.__serverSTT) {
      micProblem = 'blocked';
      $('#btnMic').title = 'Der Server meldet keine Spracherkennung — Jarvis-Fenster prüfen bzw. neu starten.';
      log('error', 'VOICE', 'Spracherkennung auf dem Server nicht verfügbar');
      updateMicButton();
      return;
    }
    try {
      Live.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false }
      });
    } catch (e) {
      var denied = /NotAllowed|Permission|denied/i.test(String(e && (e.name + e.message)));
      micProblem = 'blocked';
      $('#btnMic').title = denied
        ? 'Der Browser blockiert das Mikrofon: links in der Adressleiste aufs Schloss-Symbol klicken → Mikrofon „Zulassen“, dann F5.'
        : 'Kein Mikrofon gefunden: ' + String(e).slice(0, 80);
      log('error', 'VOICE', denied ? 'Mikrofon im Browser blockiert — Schloss-Symbol links in der Adressleiste → Mikrofon zulassen, dann F5'
                                   : 'Mikrofon nicht verfügbar: ' + String(e).slice(0, 70));
      updateMicButton();
      return;
    }
    micProblem = null;
    $('#btnMic').title = 'Klicken: Live an/aus · Gedrückt halten: sprechen (Push-to-Talk)';
    Live.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (Live.ctx.state === 'suspended') {
      // Started from boot without a click: the browser may deliver silence until the
      // first click/key. With the microphone already open Safari often lets the context
      // run anyway — so ask first and only ask for the tap if it really stays shut.
      var ctx = Live.ctx;
      try { await Promise.race([ctx.resume(), new Promise(function (r) { setTimeout(r, 1000); })]); } catch (e) {}
      if (ctx.state !== 'running') {
        micProblem = 'needs-click';
        afterGesture(function () { Live.unlock(); });
        // it may still start on its own a moment later: then the "tap" screen goes too
        ctx.onstatechange = function () { if (ctx.state === 'running' && micProblem === 'needs-click') Live.unlock(); };
      }
    }
    Live.analyser = Live.ctx.createAnalyser();
    Live.analyser.fftSize = 1024;
    Live.data = new Uint8Array(Live.analyser.fftSize);
    var src = Live.ctx.createMediaStreamSource(Live.stream);
    src.connect(Live.analyser);
    await Cap.start(Live.ctx, src);
    if (Cap.ok) OnDevice.start(Cap.rate);

    Live.on = true;
    document.body.classList.add('live');
    $('#btnMic').classList.add('live');
    setVoiceState('CALIBRATING', 'hot');
    await Live.calibrate();
    setVoiceState('LISTENING', 'hot');
    log('ok', 'VOICE', 'live mode on — just talk, it sends when you stop');
    await Live.startTicker();
  },

  /* The mic loop is driven from the audio thread, not requestAnimationFrame:
     browsers stop animation frames in a background tab, which left him deaf
     unless the HUD was the visible tab. Worklet messages keep arriving there. */
  async startTicker() {
    var fire = function () { if (Live.on) Live.loop(); if (window.Pip) Pip.tick(); };   // Pip: sign of life for the desktop card
    try {
      var url = URL.createObjectURL(new Blob([TICK_WORKLET], { type: 'application/javascript' }));
      await Live.ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      var node = new AudioWorkletNode(Live.ctx, 'jarvis-tick', { numberOfInputs: 0, numberOfOutputs: 1 });
      var mute = Live.ctx.createGain();
      mute.gain.value = 0;
      node.connect(mute).connect(Live.ctx.destination);   // an unconnected node is never processed
      node.port.onmessage = fire;
      Live.ticker = function () { node.port.onmessage = null; try { node.disconnect(); mute.disconnect(); } catch (e) {} };
    } catch (e) {
      // No worklet: a timer still runs in a background tab, only throttled (about once a second).
      log('note', 'VOICE', 'audio worklet unavailable — background listening is slow');
      var id = setInterval(fire, 16);
      Live.ticker = function () { clearInterval(id); };
    }
  },

  /* After a click: let the audio through and re-measure the room (the first
     calibration ran on silence while the browser still held the mic back). */
  async unlock() {
    if (!Live.ctx) return;
    if (Live.ctx.state === 'suspended') { try { await Live.ctx.resume(); } catch (e) {} }
    if (Live.ctx.state !== 'running') return;
    if (micProblem === 'needs-click') {
      micProblem = null;
      await Live.calibrate();
      log('ok', 'VOICE', 'Mikrofon aktiv — einfach sprechen');
    }
    updateMicButton();
  },

  /* Sample the room for a moment and sit above it. */
  calibrate() {
    return new Promise(function (resolve) {
      var samples = [], t0 = performance.now();
      (function tick() {
        if (!Live.on) return resolve();
        samples.push(Live.level());
        if (performance.now() - t0 < 700) return setTimeout(tick, 16);   // not rAF: may start in a background tab
        samples.sort(function (a, b) { return a - b; });
        var floor = samples[Math.floor(samples.length / 2)] || 0.005;
        Live.threshold = Live.base = Math.max(floor * 3.2, 0.015);
        Live.ambient = floor;
        log('note', 'VOICE', 'noise floor ' + floor.toFixed(4)
          + ' → threshold ' + Live.threshold.toFixed(4));
        resolve();
      })();
    });
  },

  level() {
    if (!Live.analyser) return 0;
    Live.analyser.getByteTimeDomainData(Live.data);
    var sum = 0;
    for (var i = 0; i < Live.data.length; i++) {
      var v = (Live.data[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / Live.data.length);
  },

  meter(rms) {
    if (window.JarvisOrb && !Live.speaking) JarvisOrb.setLevel(rms / (Live.threshold * 5));
    var pct = Math.min(100, Math.round((rms / (Live.threshold * 4)) * 100));
    var el = $('#vuFill');
    if (el) {
      el.style.width = pct + '%';
      el.className = Live.armed ? 'vufill hot' : 'vufill';
    }
    var ml = $('#micLvl');
    if (ml) ml.style.width = pct + '%';
    updateMicButton();
  },

  loop() {
    if (!Live.on) return;
    // Push-to-talk: the button decides when the sentence ends, not the silence detector.
    if (Live.ptt) { Live.meter(Live.level()); return; }
    // While JARVIS talks, only listen for a barge-in: speech clearly above the
    // room (echo cancellation removes most of his own voice) is recorded and
    // only stops him if it contains "Jarvis" (see Barge). This also covers the
    // stretch where he is already talking while Claude is still writing —
    // interrupting then cancels the rest of the answer too.
    if (Live.speaking || Barge.rec) {
      if (pendingConfirm) { Live.meter(0); return; }
      var lvl = Live.level();
      Live.meter(lvl);
      var t = performance.now();
      BargeLog.frame(lvl, t);
      if (Barge.rec) { Barge.track(lvl, t); return; }
      if (Output.target && !Speech.paused) {
        var limit = bargeNow();
        if (!Live.echoSince && lvl > bargeLevel()) Live.echoSince = t;
        var warming = Live.echoSince && t - Live.echoSince < ECHO_WARMUP_MS;
        Live.echo = warming || lvl <= limit ? Math.max(lvl, (Live.echo || 0) * ECHO_DECAY) : (Live.echo || 0) * ECHO_DECAY;
        if (warming) return;
      }
      if (lvl > bargeNow()) {
        // Record from the first loud frame — waiting first would cut "Jar" off "Jarvis".
        if (!Ringing.on) { Barge.start(t); return; }
        if (!Live.bargeSince) Live.bargeSince = t;
        else if (t - Live.bargeSince > BARGE_MS && interruptSpeech()) {   // the alarm stops on any voice — "reden = aus"
          Live.bargeSince = 0;
          Live.start(t - BARGE_MS);   // what follows still needs "Jarvis" to count
        }
      } else {
        Live.bargeSince = 0;
      }
      return;
    }
    BargeLog.answerDone();
    Live.bargeSince = 0;
    // While thinking, only listen for "I wasn't finished": if he talks again
    // shortly after a sentence was sent, drop that run and record the rest —
    // send() then glues both parts together. Otherwise stay deaf — except
    // during the startup briefing, see below.
    if (running && !pendingConfirm) {
      var since = Live.lastSent ? performance.now() - Live.lastSent.at : Infinity;
      // Briefing: normal speech (sustained, a bit above the room) cancels it —
      // The user must be able to just start talking. Short noises don't count.
      if (briefingActive && since >= CONTINUE_MS) {
        var bl = Live.level(), bt = performance.now();
        Live.meter(bl);
        if (Live.armed) Live.discard();
        if (bl > Math.max(Live.threshold * BRIEFING_BARGE_FACTOR, 0.012)) {
          if (!Live.bargeSince) Live.bargeSince = bt;
          else if (bt - Live.bargeSince > BARGE_MS) {
            Live.bargeSince = 0;
            briefingActive = false;
            log('note', 'VOICE', 'Briefing abgebrochen — ich höre');
            cancelRun();
            Live.start(bt - BARGE_MS);
          }
        } else Live.bargeSince = 0;
        return;
      }
      if (since < CONTINUE_MS) {
        var lv = Live.level(), tn = performance.now();
        Live.meter(lv);
        if (Live.armed || lv > Live.threshold) {
          if (!Live.armed && !Live.contSince) { Live.contSince = tn; return; }
          if (Live.armed || tn - Live.contSince > CONTINUE_SUSTAIN_MS) {
            Live.contSince = 0;
            Live.continuing = true;
            cancelRun();
            log('note', 'VOICE', 'Sie reden weiter — warte auf den Rest');
            if (!Live.armed) Live.start(tn - CONTINUE_SUSTAIN_MS);
            else setVoiceState('HEARING', 'hot');
          }
        } else Live.contSince = 0;
        return;
      }
      if (Live.armed) Live.discard();
      Live.meter(0);
      return;
    }
    var rms = Live.level();
    Live.ambient += (rms - Live.ambient) * AMBIENT_RATE;
    Live.threshold = Math.max(Live.base, Live.ambient * AMBIENT_FACTOR);
    Ambience.hear(rms > Live.base * 2);   // base alone: the iPad's gain control lifts a quiet room over it
    Live.meter(rms);
    var now = performance.now();
    if (rms > Live.threshold) Live.lastVoice = now;

    if (!Live.armed) {
      if (rms > Live.threshold && now - (Live.spokeAt || 0) > ECHO_TAIL_MS) Live.start(now);
      return;
    }
    // Nobody talks 20 s without a breath — that is music or TV, so drop it.
    if (now - Live.voiceStart > MAX_SPEECH_MS) {
      log('note', 'VOICE', 'Dauergeräusch verworfen (Musik?)');
      return Live.discard();
    }
    if (now - Live.lastVoice > SILENCE_MS) {
      if (now - Live.voiceStart - SILENCE_MS > MIN_SPEECH_MS) Live.finish();
      else Live.discard();
    }
  },

  start(now) {
    // Was this meant for him? Decided in send(): awake when it started, the
    // server heard "Hey Jarvis" meanwhile, or the transcript holds "Jarvis".
    var rec = { at: now, awake: Wake.awake() || Live.ptt };
    if (Cap.ok) {
      Live.take = Cap.begin();
      Live.rec = rec;
      Live.armed = true;
      Live.voiceStart = now;
      Live.lastVoice = now;
      setVoiceState('HEARING', 'hot');
      return;
    }
    try {
      Live.chunks = [];
      Live.recorder = new MediaRecorder(Live.stream);
      Live.recorder.ondataavailable = function (e) { if (e.data.size) Live.chunks.push(e.data); };
      Live.recorder.onstop = function () {
        var blob = new Blob(Live.chunks, { type: Live.recorder.mimeType || 'audio/webm' });
        if (Live.sending) { Live.sending = false; Live.send(blob, rec); }
      };
      Live.recorder.start();
      Live.armed = true;
      Live.voiceStart = now;
      Live.lastVoice = now;
      setVoiceState('HEARING', 'hot');
    } catch (e) {
      log('error', 'VOICE', 'recorder failed: ' + String(e).slice(0, 60));
      Live.armed = false;
    }
  },

  finish() {
    Live.armed = false;
    if (Live.rec) Live.rec.dur = performance.now() - Live.voiceStart - SILENCE_MS;
    setVoiceState('TRANSCRIBING', 'hot');
    if (Live.take) {
      var blob = Cap.end(Live.take, true);
      Live.take = null;
      Live.send(blob, Live.rec);
      return;
    }
    Live.sending = true;
    try { Live.recorder.stop(); } catch (e) { Live.sending = false; }
  },

  discard() {
    Live.armed = false;
    Live.sending = false;
    if (Live.take) { Cap.end(Live.take, false); Live.take = null; }
    try { if (Live.recorder && Live.recorder.state === 'recording') Live.recorder.stop(); } catch (e) {}
    setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
  },

  async send(blob, rec) {
    // Heard on the device and no "Jarvis" in it: the clip stays here, the PC never gets it.
    // (push-to-talk counts as awake through rec.awake)
    if (OnDevice.gate() && !pendingConfirm && !Live.partial && !Live.continuing
        && !(rec && (rec.awake || Wake.lastHeard >= rec.at - 2500))) {
      var peak = OnDevice.peak(rec ? rec.at - 2500 : 0), need = OnDevice.need(rec && rec.dur || 0);
      var all = OnDevice.peaks(rec ? rec.at - 2500 : 0);
      var info = 'Wert ' + peak.toFixed(3) + ', Schwelle ' + need.toFixed(3) + ', ' + Math.round((rec && rec.dur || 0) / 100) / 10 + ' s'
        + (Ambience.kind() ? ', ' + Ambience.kind() : '') + (all ? ' · ' + all : '');
      if (peak < need) {
        if (peak >= 0.01) log('note', 'WAKE', 'kein „Jarvis“ erkannt (' + info + ')');
        WakeLog.send('verworfen · ' + info);
        setVoiceState('LISTENING', 'hot');
        return;
      }
      WakeLog.send('an den PC · ' + info);
      // The model was sure (a German "Jarvis" in a sentence reached 0.34) but Whisper may still
      // spell it as something else — then the device's ear counts, not the transcript's spelling.
      if (rec && OnDevice.trusted(peak, rec.dur || 0)) rec.deviceHeard = true;
    }
    try {
      var res = await fetch('/api/listen', {
        method: 'POST',
        headers: headers({ 'content-type': blob.type || 'audio/webm' }),
        body: blob
      });
      var data = await res.json();
      var text = (data && data.text || '').trim();
      // Nothing is logged before it is clear the words were meant for him: room talk,
      // TV and phone calls are transcribed locally to look for "Jarvis", then dropped.
      if (!res.ok || !text || text.replace(/[^\p{L}\p{N}]/gu, '').length < 2 || NOISE_TEXT.test(text)) {
        if (OnDevice.gate()) WakeLog.send('PC: nichts verstanden');
        setVoiceState('LISTENING', 'hot');
        return;
      }
      if (pendingConfirm) {
        log('note', 'VOICE', 'heard: "' + text + '"');
        // Deliberate words only, so a stray "ja" from the room cannot approve.
        if (/best(ä|ae)tig/i.test(text) && !/nicht|kein/i.test(text)) answerConfirm(true);
        else if (/\b(nein|stopp?|abbrechen|ablehnen|nicht)\b/i.test(text)) answerConfirm(false);
        else log('note', 'FREIGABE', 'sag "bestätigt" oder "nein"');
        setVoiceState('LISTENING', 'hot');
        return;
      }
      // Wake word: room talk, TV and phone calls stay with the room.
      if (Wake.mode() !== 'off' && !Live.partial && !Live.continuing) {
        var heardWake = !!(rec && (rec.awake || rec.deviceHeard || Wake.lastHeard >= rec.at - 2500));
        var w = Wake.check(text, heardWake, Ambience.strict());
        if (!w.ok) {
          if (OnDevice.gate()) WakeLog.send('PC: kein „Jarvis“ im Text (' + text.split(/\s+/).length + ' Wörter)');
          setVoiceState('LISTENING', 'hot');
          return;
        }
        log('note', 'VOICE', 'heard: "' + text + '"');
        MusicHold.hold();                          // text mode: quiet for his answer and what follows
        text = w.text;
        if (text.replace(/[^\p{L}\p{N}]/gu, '').length < 2) {   // just "Jarvis?" — he is listening now
          Wake.open(Wake.cfg.window_s || 7);
          wakeChime();
          log('note', 'WAKE', 'ich höre');
          setVoiceState('LISTENING', 'hot');
          return;
        }
      } else {
        log('note', 'VOICE', 'heard: "' + text + '"');   // no wake word set, or the rest of a sentence he is already on
      }
      // A held-back unfinished start ("Als Allererstes mal bitte, ähm,") gets the rest glued on.
      if (Live.partial) {
        text = Live.partial + ' ' + text;
        Live.partial = null;
        clearTimeout(Live.partialTimer);
      }
      // Sounds unfinished? Don't send yet — wait a moment for the rest of the sentence.
      // Push-to-talk always sends: there the button said the sentence is over.
      if (!Live.pttSend && looksUnfinished(text)) {
        Live.partial = text;
        log('note', 'VOICE', 'klingt unfertig — warte auf den Rest: "' + text.slice(-40) + '"');
        setVoiceState('LISTENING', 'hot');
        var holdUntil = performance.now() + UNFINISHED_HOLD_MS;
        (function waitForRest() {
          Live.partialTimer = setTimeout(function () {
            if (!Live.partial) return;
            if (Live.armed || Live.sending || performance.now() < holdUntil) return waitForRest();
            var t = Live.partial;
            Live.partial = null;
            Live.lastSent = { text: t, at: performance.now() };
            transmit(t).then(function () { setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : ''); });
          }, 200);
        })();
        return;
      }
      Live.pttSend = false;
      // He kept talking after a pause: the earlier half belongs to this sentence.
      if (Live.continuing && Live.lastSent) text = Live.lastSent.text + ' ' + text;
      Live.continuing = false;
      Live.lastSent = { text: text, at: performance.now() };
      await transmit(text);
      setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
    } catch (e) {
      log('error', 'VOICE', 'transcription failed: ' + String(e).slice(0, 70));
      setVoiceState('LISTENING', 'hot');
    }
  },

  disable() {
    if (typeof VoiceClient !== 'undefined' && VoiceClient.on) VoiceClient.stop();
    Live.on = false;
    Live.armed = false;
    Live.ptt = false;
    micProblem = null;
    if (Live.ticker) { Live.ticker(); Live.ticker = null; }
    try { if (Live.recorder && Live.recorder.state === 'recording') Live.recorder.stop(); } catch (e) {}
    Barge.drop();
    if (Live.take) { Cap.end(Live.take, false); Live.take = null; }
    Cap.stop();
    OnDevice.stop();
    if (Live.stream) Live.stream.getTracks().forEach(function (t) { t.stop(); });
    if (Live.ctx) { try { Live.ctx.close(); } catch (e) {} }
    Live.stream = Live.ctx = Live.analyser = null;
    document.body.classList.remove('live');
    $('#btnMic').classList.remove('live');
    Live.meter(0);
    setVoiceState('IDLE', '');
    log('note', 'VOICE', 'live mode off');
  }
};

/* Scale the quiet-room threshold, not the live one: with TV or music on the
   live threshold already sits above the background. */
/* With a Sonos answering, also above his own voice as the mic hears it from the room. */
function bargeNow() {
  return Output.target ? Math.max(bargeLevel(), (Live.echo || 0) * ECHO_FACTOR) : bargeLevel();
}

/* The clip is mostly words he is saying himself: the Sonos, not the user. */
function echoOf(text) {
  var said = stripTones(answerText || '').toLowerCase();
  var words = (text.toLowerCase().match(/\p{L}{3,}/gu) || []);
  if (!words.length) return false;
  var hits = words.filter(function (w) { return said.indexOf(w) >= 0; }).length;
  return hits / words.length >= 0.6;
}

function bargeLevel() {
  return Math.max(Live.base * BARGE_FACTOR, Live.ambient * BARGE_AMBIENT, BARGE_MIN);
}

/* What talking over him looked like, for logs/wake_device.log (one line per answer that someone tried
   to interrupt, one per checked clip): the loudest moment against the bar, and what Whisper heard.
   The numbers are what the thresholds above get tuned with. */
var BargeLog = {
  st: null,
  frame: function (lvl, t) {
    var s = BargeLog.st || (BargeLog.st = { since: t, peak: 0, bar: 0, over: 0, clips: 0, drops: 0 });
    if (lvl > s.peak) { s.peak = lvl; s.bar = bargeNow(); }
    if (lvl > bargeNow()) s.over++;
  },
  answerDone: function () {
    var s = BargeLog.st;
    BargeLog.st = null;
    if (!s || !(s.clips || s.drops || s.peak > s.bar * 0.5)) return;
    BargeLog.send('Antwort ' + ((performance.now() - s.since) / 1000).toFixed(1) + ' s · lautester Moment '
      + s.peak.toFixed(3) + ', Hürde ' + s.bar.toFixed(3) + ' (Raum ' + Live.base.toFixed(3) + ', Hintergrund '
      + Live.ambient.toFixed(3) + (Output.target ? ', Echo ' + (Live.echo || 0).toFixed(3) : '') + ') · '
      + s.clips + ' Clips geprüft, ' + s.drops + ' kurze Laute');
  },
  send: function (text) {
    WakeLog.send('Unterbrechen: ' + text + (Output.target ? ' [' + Output.label + ']' : ''), true);
  }
};

/* Barge-in by name: talking over JARVIS records a clip while he keeps talking.
   "Jarvis" in it ends his answer (and whatever followed is the new request);
   anything else — a cough, the TV, talk in the room — and he never noticed.
   (Pausing on every voice made him stutter at each "mhm".) */
var WAKE_ANY_RE = new RegExp('(^|[^\\p{L}])' + WAKE_NAME + '(?![\\p{L}])', 'iu');
var Barge = {
  rec: null, chunks: [], since: 0, lastVoice: 0, busy: false,

  start(t) {
    if (Barge.busy || !Live.stream) return;
    if (Cap.ok) {
      Barge.rec = { take: Cap.begin() };
      Barge.since = t;
      Barge.lastVoice = t;
      return;
    }
    var r;
    try { r = new MediaRecorder(Live.stream); } catch (e) { return; }
    Barge.rec = r;
    Barge.chunks = [];
    Barge.since = t;
    Barge.lastVoice = t;
    r.ondataavailable = function (e) { if (e.data.size) Barge.chunks.push(e.data); };
    r.onstop = function () {
      if (!Barge.busy) return;   // dropped, not finished
      Barge.check(new Blob(Barge.chunks, { type: r.mimeType || 'audio/webm' }), Barge.cut);
    };
    r.start();
  },

  track(lvl, t) {
    if (lvl > bargeNow()) Barge.lastVoice = t;
    if (!Speech.paused && Barge.lastVoice - Barge.since > PAUSE_MS) pauseSpeech();
    var cut = t - Barge.since > (Speech.paused ? BARGE_MAX_PAUSED_MS : BARGE_MAX_MS);
    if (cut || t - Barge.lastVoice > (Speech.paused ? BARGE_SILENCE_PAUSED_MS : BARGE_SILENCE_MS)) {
      if (Barge.lastVoice - Barge.since <= BARGE_MS) {   // a blip, not speech: nothing to check
        if (BargeLog.st) BargeLog.st.drops++;
        return Barge.drop();
      }
      if (BargeLog.st) BargeLog.st.clips++;
      var r = Barge.rec;
      Barge.rec = null;
      Barge.cut = cut;
      Barge.busy = true;
      if (r.take) return Barge.check(Cap.end(r.take, true), cut);
      try { r.stop(); } catch (e) { Barge.busy = false; if (Speech.paused) resumeSpeech(); }
    }
  },

  drop() {
    var r = Barge.rec;
    Barge.rec = null;
    Barge.busy = false;
    if (Speech.paused) resumeSpeech();   // nothing will be checked: he must not wait for good
    if (r && r.take) { Cap.end(r.take, false); return; }
    try { if (r && r.state === 'recording') r.stop(); } catch (e) {}
  },

  async check(blob, cut) {
    var text = '';
    var paused = Speech.paused;   // he stopped for it: whatever was said is checked, "Jarvis" or not
    // talking over him without "Jarvis" heard on the device: nothing to check on the PC
    if (!paused && OnDevice.gate() && Math.max(Wake.lastHeard, OnDevice.faintAt) < performance.now() - 4000
        && OnDevice.peak(Barge.since - PREROLL_MS) < OnDevice.SHORT_FLOOR) {
      BargeLog.send('nicht geprüft, das Gerät hörte kein „Jarvis“ (Wert ' + OnDevice.peak(Barge.since - PREROLL_MS).toFixed(3) + ')');
      Barge.busy = false;
      return;
    }
    try {
      var res = await fetch('/api/listen', {
        method: 'POST', headers: headers({ 'content-type': blob.type || 'audio/webm' }), body: blob
      });
      var data = await res.json();
      if (res.ok) text = (data && data.text || '').trim();
    } catch (e) { /* unreadable clip: he simply keeps talking */ }
    Barge.busy = false;
    var named = WAKE_ANY_RE.test(text);
    BargeLog.send('gehört „' + text.slice(0, 80) + '“ → ' + (named ? 'Name erkannt, unterbrochen' : paused ? 'ohne Namen, in der Pause' : 'ohne Namen, weiter'));
    var rest = named ? Wake.check(text, true).text : text.trim();
    var words = rest.replace(/[^\p{L}\p{N}]/gu, '').length >= 2 && !BARGE_FILLER.test(rest);
    // Without his name it counts only when he paused for it, with real words, no TV talking and not his own voice.
    if (!named && (!paused || !words || /^(tv|sport)$/.test(Ambience.kind()) || echoOf(text))) {
      if (paused) resumeSpeech();   // nothing for him: he goes on where he was
      return;
    }
    log('note', 'VOICE', 'heard: "' + text + '"');
    interruptSpeech();
    if (running) cancelRun();
    // "Jarvis, wie wird das Wetter?" in one breath is the next request; a clip cut
    // off mid-sentence or a bare "Jarvis" / "Jarvis, stopp" just opens the mic.
    if (!cut && !BARGE_STOP_ONLY.test(rest) && rest.replace(/[^\p{L}\p{N}]/gu, '').length >= 2) {
      Live.lastSent = { text: rest, at: performance.now() };
      await transmit(rest);
      setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
      return;
    }
    Wake.open(Wake.cfg.window_s || 7);
    wakeChime();
    log('note', 'WAKE', 'ich höre');
    setVoiceState('LISTENING', 'hot');
  }
};

/* Live button:
   - short click: Live on/off. If Live is already on but the browser still
     holds the mic back (auto-start without a click), the click unlocks it
     instead of switching it off.
   - hold: push-to-talk. Records while held, sends on release. Works while
     JARVIS talks or thinks too (it cuts him off). */
async function toggleLive() {
  if (!Live.on) {
    micProblem = null;
    VoiceOwner.take();
    localStorage.setItem('jarvis.live', '1');
    await Live.enable();
  } else if (micProblem === 'needs-click' || (Live.ctx && Live.ctx.state === 'suspended')) {
    await Live.unlock();
  } else {
    Live.disable();
    localStorage.setItem('jarvis.live', '0');
  }
  updateMicButton();
}

async function startPTT() {
  if (!Live.on) { await Live.enable(); if (!Live.on) return; }
  if (typeof VoiceClient !== 'undefined' && VoiceClient.on) {
    Live.ptt = true; VoiceClient.ptt(true); setVoiceState('HEARING', 'hot'); updateMicButton();
    log('note', 'VOICE', 'Push-to-Talk — loslassen zum Senden');
    return;
  }
  await Live.unlock();
  if (Live.speaking || Speech.active) interruptSpeech();
  if (running) cancelRun();
  briefingActive = false;
  Live.ptt = true;
  if (!Live.armed) Live.start(performance.now());
  setVoiceState('HEARING', 'hot');
  log('note', 'VOICE', 'Push-to-Talk — loslassen zum Senden');
}

function stopPTT() {
  if (!Live.ptt) return;
  Live.ptt = false;
  if (typeof VoiceClient !== 'undefined' && VoiceClient.on) { VoiceClient.ptt(false); updateMicButton(); return; }
  Live.pttSend = true;          // released button = sentence is over, send without waiting
  if (Live.armed) Live.finish();
  updateMicButton();
}

function setupMic() {
  var btn = $('#btnMic'), holdTimer = null, held = false, x0 = 0, y0 = 0;
  btn.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    held = false; x0 = e.clientX; y0 = e.clientY;
    btn.setPointerCapture && btn.setPointerCapture(e.pointerId);
    holdTimer = setTimeout(function () { holdTimer = null; held = true; startPTT(); }, PTT_HOLD_MS);
  });
  // A finger that moves is a swipe, not a press: the iPad's swipe up from the bottom edge
  // starts on this button and used to begin push-to-talk or switch Live on and off.
  btn.addEventListener('pointermove', function (e) {
    if (holdTimer && Math.hypot(e.clientX - x0, e.clientY - y0) > 12) { clearTimeout(holdTimer); holdTimer = null; }
  });
  var release = function () {
    if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; toggleLive(); }
    else if (held) { held = false; stopPTT(); }
  };
  btn.addEventListener('pointerup', release);
  btn.addEventListener('pointercancel', function () { if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } stopPTT(); });
  // Keyboard (Enter/Space on the focused button) still toggles.
  btn.addEventListener('click', function (e) { if (e.detail === 0) toggleLive(); });
  btn.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  updateMicButton();
}

/* ── station ──────────────────────────────────
   A tablet that stays in one place (the iPad on its stand) becomes a second
   Jarvis with screen and microphone: command-centre view, screen kept on, mic
   open for "Jarvis" from the start. Remembered per device. Notices stay silent
   there like on any remote device — the PC reads them out.
   Night: while the sensor "station_night" (config/home.json, the iPad's focus) is
   on, the screen is black with a faint clock. The mic keeps listening; a
   conversation or a tap lifts the black for a moment. Locking the iPad is no
   option — iOS cuts the mic of a locked page. */
var Station = {
  on: (function () { try { return localStorage.getItem('jarvis.station') === '1'; } catch (e) { return false; } })(),
  lock: null, peekTimer: 0,

  init: function () {
    var btn = $('#btnStation');
    if (btn) btn.onclick = function () { Station.set(!Station.on); };
    var shade = $('#nightShade');
    if (shade) shade.onclick = function () {   // a look at the HUD without a word
      document.body.classList.add('night-peek');
      clearTimeout(Station.peekTimer);
      Station.peekTimer = setTimeout(function () { document.body.classList.remove('night-peek'); }, 20000);
    };
    document.addEventListener('visibilitychange', function () {
      // iOS drops the wake lock whenever the page is hidden
      if (Station.on && document.visibilityState === 'visible') Station.keepAwake();
    });
    Station.apply();
  },

  set: function (on) {
    Station.on = on;
    try { localStorage.setItem('jarvis.station', on ? '1' : '0'); } catch (e) { /* private mode */ }
    if (window.setView) window.setView(on ? 'wall' : '');
    Station.apply();
    // Called from a tap: the mic and audio unlock right here, no second tap needed.
    if (on && !Live.on) toggleLive();
    log('note', 'STATION', on ? 'Station an — Bildschirm bleibt an, sag „Jarvis …“' : 'Station aus');
  },

  apply: function () {
    document.body.classList.toggle('station', Station.on);
    var btn = $('#btnStation');
    if (btn) btn.classList.toggle('on', Station.on);
    if (Station.on) Station.keepAwake();
    else if (Station.lock) { Station.lock.release().catch(function () {}); Station.lock = null; }
  },

  night: function (on) {
    var was = document.body.classList.contains('night');
    document.body.classList.toggle('night', on);
    if (on !== was && Station.on) log('note', 'STATION', on ? 'Nachtbild an' : 'Nachtbild aus — guten Morgen');
  },

  /* Screen Wake Lock: Safari 16.4+, in a home-screen app from iPadOS 18.4. Older
     ones fall back to the iPad setting "Automatische Sperre: Nie". */
  keepAwake: async function () {
    if (!('wakeLock' in navigator) || (Station.lock && !Station.lock.released)) return;
    try {
      Station.lock = await navigator.wakeLock.request('screen');
    } catch (e) {
      log('note', 'STATION', 'Bildschirm-Sperre nicht abschaltbar — Einstellungen → Anzeige → Automatische Sperre: Nie');
    }
  }
};

/* ── on start ─────────────────────────────────
   Mic on by itself (the browser remembers the permission after the first "allow")
   and a short briefing on start — once per day, so a string of restarts does
   not repeat it. Talking while it is prepared cancels it. */
var BRIEFING_RESET = '7';
var briefingActive = false;
// The server assembles the facts itself (weather/golf, appointments, solar,
// mail — no stock news) and has them phrased in one small call.
var BRIEFING = 'Tagesbriefing zum Start';

/* iPad, not iPhone — iPadOS Safari calls itself a Mac, a touch screen there is the giveaway (as pair.html) */
function isTablet() {
  return /iPad/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

async function bootSequence() {
  await loadStatus();
  if (!(await VoiceOwner.decide())) return;     // another tab listens, speaks and briefs
  // A phone starts quiet: no open microphone in the pocket, no briefing the PC already
  // gave — the mic button (tap = live, hold = push-to-talk) does it. The iPad stays in
  // one place and listens for "Jarvis" on every start, after a restart too — switching
  // it off by hand once used to stick and left it silent after the next restart.
  if (window.__remote) {
    if ((Station.on || isTablet()) && window.__serverSTT) {
      document.body.classList.add('autolive');   // iOS may still want one tap: the whole screen says so
      Live.enable();
    }
    return;
  }
  if (window.__serverSTT && localStorage.getItem('jarvis.live') !== '0') Live.enable();
  var today = new Date().toLocaleDateString('sv-SE');   // YYYY-MM-DD, local time
  // Bump BRIEFING_RESET to get one extra briefing on the next load (for testing).
  if (localStorage.getItem('jarvis.briefingReset') !== BRIEFING_RESET) {
    localStorage.setItem('jarvis.briefingReset', BRIEFING_RESET);
    localStorage.removeItem('jarvis.briefingDay');
  }
  if (localStorage.getItem('jarvis.briefingDay') === today) {
    log('note', 'BRIEFING', 'übersprungen — heute schon gehabt');
    return;
  }
  localStorage.setItem('jarvis.briefingDay', today);
  briefingActive = true;
  try { await transmit(BRIEFING); } finally { briefingActive = false; }
}

/* ── devices (remote access) ───────────────────
   Only on the PC: a six-digit code for a new iPhone/iPad (typed on its
   /pair.html), and the list of paired devices, each removable. */
var Devices = {
  timer: null,
  api: async function (path, body) {
    var r = await fetch(path, { method: 'POST', headers: headers({ 'Content-Type': 'application/json' }),
                                body: JSON.stringify(body || {}) });
    return r.json();
  },
  open: async function () {
    $('#pairBox').hidden = false;
    $('#pairCode').textContent = '······';
    $('#pairHint').textContent = '';
    await Devices.list();
  },
  close: function () { $('#pairBox').hidden = true; clearInterval(Devices.timer); },
  newCode: async function () {
    var d = await Devices.api('/api/pair/start');
    $('#pairCode').textContent = d.code.slice(0, 3) + ' ' + d.code.slice(3);
    var until = Date.now() + d.ttl * 1000;
    clearInterval(Devices.timer);
    var tick = function () {
      var left = Math.max(0, Math.round((until - Date.now()) / 1000));
      $('#pairHint').textContent = left ? 'Auf dem Gerät eingeben · noch ' + Math.floor(left / 60) + ':'
        + String(left % 60).padStart(2, '0') : 'Abgelaufen — neuen Code holen.';
      if (!left) { $('#pairCode').textContent = '······'; clearInterval(Devices.timer); Devices.list(); }
    };
    tick();
    Devices.timer = setInterval(function () { tick(); }, 1000);
    if (!d.remote) $('#pairHint').textContent = 'Fernzugriff ist aus: JARVIS_REMOTE=1 in .env setzen.';
  },
  list: async function () {
    var d = await Devices.api('/api/devices'), ul = $('#pairList');
    ul.innerHTML = d.devices.length ? '' : '<li class="empty">Noch kein Gerät gekoppelt.</li>';
    d.devices.forEach(function (dev) {
      var li = document.createElement('li');
      li.innerHTML = '<b>' + esc(dev.name) + '</b><em>' + esc(dev.seen ? 'zuletzt ' + dev.seen : 'gekoppelt ' + dev.created)
        + '</em><button title="Gerät entfernen">✕</button>';
      li.querySelector('button').onclick = async function () {
        if (!confirm(dev.name + ' entfernen? Es muss danach neu gekoppelt werden.')) return;
        await Devices.api('/api/devices/revoke', { id: dev.id });
        Devices.list();
      };
      ul.appendChild(li);
    });
  }
};

function setupDevices() {
  if (window.__remote) return;
  $('#btnPair').onclick = Devices.open;
  $('#pairNew').onclick = Devices.newCode;
  $('#pairClose').onclick = Devices.close;
}

/* ── boot ─────────────────────────────────── */
window.addEventListener('DOMContentLoaded', function () {
  graph = new MemoryGraph($('#graph'), {
    onSelect: function (node, trace) { renderInspector(node, trace); }
  });
  window.__jarvisGraph = graph;   // handy from devtools while tuning layout

  // dial ticks
  var ticks = '';
  for (var i = 0; i < 60; i++) {
    var a = (i / 60) * Math.PI * 2, r1 = 92, r2 = i % 5 === 0 ? 82 : 87;
    ticks += '<line x1="' + (100 + Math.cos(a) * r1).toFixed(1) + '" y1="' + (100 + Math.sin(a) * r1).toFixed(1)
      + '" x2="' + (100 + Math.cos(a) * r2).toFixed(1) + '" y2="' + (100 + Math.sin(a) * r2).toFixed(1)
      + '" stroke="rgba(95,228,255,' + (i % 5 === 0 ? '.35' : '.15') + ')" stroke-width="1"/>';
  }
  $('#ticks').innerHTML = ticks;

  loadGraph(false);
  tickClock();
  setInterval(tickClock, 10000);
  loadDashboard();
  setTimeout(loadDashboard, 4000);   // first call only starts the server-side fetches
  setInterval(loadDashboard, 30000);
  var logPanel = $('#pLog');
  logPanel.classList.toggle('closed', localStorage.getItem('jarvis.log') !== 'open');
  logPanel.querySelector('h2').onclick = function () {
    logPanel.classList.toggle('closed');
    localStorage.setItem('jarvis.log', logPanel.classList.contains('closed') ? 'closed' : 'open');
  };
  setupMic();
  Station.init();
  Output.init();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(function () {});
  setupDevices();
  setVoiceState('IDLE', '');
  connectEvents();
  setInterval(Timers.render, 1000);
  bootSequence();

  // Restart: the server exits like after a code change and the supervisor starts it
  // again; the page reloads itself once the new boot id shows up (Reconnect).
  var rb = $('#btnRestart');
  if (rb) rb.onclick = async function () {
    if (!confirm('Jarvis-Server neu starten? Ein laufendes Gespräch wird abgebrochen.')) return;
    rb.disabled = true;
    try {
      var r = await (await fetch('/api/restart', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
                                                    body: '{}' })).json();
      log(r.ok ? 'note' : 'error', 'SERVER', r.ok ? 'Neustart — gleich wieder da' : r.error);
      if (!r.ok) { rb.disabled = false; return; }
    } catch (e) { log('note', 'SERVER', 'Neustart läuft'); }
    Reconnect.lost();
  };

  LiveBar.start();

  // Stop: only lit while he is thinking or talking; cuts both off.
  $('#btnStop').onclick = function () {
    cancelRun();
    briefingActive = false;
    setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
    log('note', 'VOICE', 'per Knopf abgebrochen');
  };
  setInterval(function () {
    $('#btnStop').disabled = !(running || stopSpeaking || Speech.active);
  }, 200);

  $('#btnSend').onclick = function () { var v = $('#ask').value; $('#ask').value = ''; transmit(v, { typed: true }); };
  $('#ask').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { var v = $('#ask').value; $('#ask').value = ''; transmit(v, { typed: true }); }
  });
  $('#btnFit').onclick = function () { graph.fit(); };
  $('#btnLabels').onclick = function () {
    graph.showLabels = !graph.showLabels;
    $('#btnLabels').classList.toggle('on', graph.showLabels);
  };
  $('#btnDim').onclick = function () {
    graph.dim = !graph.dim;
    $('#btnDim').classList.toggle('on', graph.dim);
  };
  $('#btnLabels').classList.add('on');

  document.addEventListener('keydown', function (e) {
    if (e.key === ' ' && document.activeElement !== $('#ask') && interruptSpeech()) {
      e.preventDefault();
      return;
    }
    if (e.key === 'Escape') {
      cancelRun();
      graph.setFocus(null);
    }
    if (e.key === '/' && document.activeElement !== $('#ask')) { e.preventDefault(); $('#ask').focus(); }
  });

  setInterval(loadStatus, 20000);
  loadLive();
  setInterval(loadLive, 30000);
  log('ok', 'BOOT', 'HUD online · claude code · local vault');
});

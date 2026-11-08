/* Thin client of the voice engine (jarvis/voice/voice_engine.py, served by voice_ws.py).

   The browser does only what has to happen next to the hardware: it opens the microphone, sends it
   as 20-ms frames of 16-kHz PCM over a WebSocket, and plays the PCM the server sends back through an
   AudioWorklet with a ring buffer, a gain node for ducking and 30-ms fades, and reports which
   sentence it is playing. Listening, turn-taking, barge-in and the model run live on the server;
   this file only mirrors the server's state into the HUD (app.js: Live, running, bubbles, log).

   app.js calls: VoiceClient.wanted() · start() · stop() · send(text, typed) · say(text) · stopAll() ·
   ptt(on) · wakeHeard(). Without a running engine (status.voice_engine.running false) everything
   stays with the old browser pipeline. */
var VoiceClient = {
  on: false, ws: null, ctx: null, stream: null, cap: null, player: null, gain: null,
  ready: false, state: 'IDLE', cfg: null, reconnectTimer: null, sayWaiters: [], pending: null,
  speakDoneWaiters: [], lastAmbience: '', openedAt: 0,

  wanted: function () {
    var e = window.__voiceEngine;
    return !!(e && e.enabled && e.running && !window.__remote);   // the PC first; the iPad keeps the old path
  },

  url: function () {
    var e = window.__voiceEngine || {};
    if (e.ws_url) return e.ws_url;
    return 'ws://127.0.0.1:' + (e.port || 8724);
  },

  /* ── capture ─────────────────────────────── */
  CAP_WORKLET: 'registerProcessor("jarvis-engine-cap", class extends AudioWorkletProcessor {'
    + 'constructor(o) { super(); this.rate = sampleRate; this.phase = 0; this.acc = 0; this.cnt = 0; this.out = new Int16Array(320); this.n = 0; }'
    + 'process(inputs) { var ch = inputs[0] && inputs[0][0]; if (!ch) return true;'
    + ' for (var i = 0; i < ch.length; i++) { this.acc += ch[i]; this.cnt++; this.phase += 16000;'
    + '  if (this.phase < this.rate) continue; this.phase -= this.rate;'
    + '  var v = Math.max(-1, Math.min(1, this.acc / this.cnt)); this.acc = 0; this.cnt = 0;'
    + '  this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;'
    + '  if (this.n === 320) { this.port.postMessage(this.out.buffer, [this.out.buffer]); this.out = new Int16Array(320); this.n = 0; } }'
    + ' return true; } });',

  /* ── playback ────────────────────────────── */
  PLAY_WORKLET: 'registerProcessor("jarvis-engine-play", class extends AudioWorkletProcessor {'
    + 'constructor() { super(); this.rate = sampleRate; this.inRate = 24000; this.q = []; this.pos = 0; this.frac = 0;'
    + ' this.cur = null; this.gain = 1; this.target = 1; this.step = 0; this.idle = true; this.fadeOut = 0;'
    + ' this.port.onmessage = (e) => this.msg(e.data); }'
    + 'msg(d) { if (d.t === "pcm") { this.q.push({ id: d.id, buf: new Int16Array(d.buf), end: !!d.end }); this.idle = false; }'
    + ' else if (d.t === "rate") this.inRate = d.rate;'
    + ' else if (d.t === "flush") { this.fadeOut = Math.max(1, Math.round(this.rate * (d.ms || 30) / 1000)); }'
    + ' else if (d.t === "gain") { this.target = d.value; var n = Math.max(1, Math.round(this.rate * (d.ms || 30) / 1000)); this.step = (this.target - this.gain) / n; } }'
    + 'next() { while (this.q.length) { var h = this.q[0]; if (this.pos < h.buf.length) return h;'
    + '  if (h.end) this.port.postMessage({ t: "ended", id: h.id }); this.q.shift(); this.pos = 0; } return null; }'
    + 'process(inputs, outputs) { var out = outputs[0][0]; var ratio = this.inRate / this.rate; var lastId = this.cur;'
    + ' for (var i = 0; i < out.length; i++) {'
    + '  if (this.fadeOut > 0) { this.fadeOut--; var g = this.fadeOut / Math.max(1, this.fadeOut + 1);'
    + '   var hh = this.next(); out[i] = hh ? (hh.buf[Math.floor(this.pos)] / 32768) * this.gain * g : 0;'
    + '   if (hh) { this.frac += ratio; while (this.frac >= 1) { this.frac -= 1; this.pos++; } }'
    + '   if (this.fadeOut === 0) { this.q = []; this.pos = 0; this.cur = null; this.port.postMessage({ t: "idle", flushed: true }); } continue; }'
    + '  if (this.step) { this.gain += this.step; if ((this.step > 0 && this.gain >= this.target) || (this.step < 0 && this.gain <= this.target)) { this.gain = this.target; this.step = 0; } }'
    + '  var h = this.next(); if (!h) { out[i] = 0; if (!this.idle) { this.idle = true; this.cur = null; this.port.postMessage({ t: "idle" }); } continue; }'
    + '  if (h.id !== this.cur) { this.cur = h.id; this.port.postMessage({ t: "playing", id: h.id }); }'
    + '  var p = Math.floor(this.pos), a = h.buf[p] / 32768, b = (p + 1 < h.buf.length ? h.buf[p + 1] : a) / 32768;'
    + '  out[i] = (a + (b - a) * this.frac) * this.gain; this.frac += ratio; while (this.frac >= 1) { this.frac -= 1; this.pos++; } }'
    + ' for (var c = 1; c < outputs[0].length; c++) outputs[0][c].set(out); return true; } });',

  log: function (kind, msg) { if (typeof log === 'function') log(kind, 'ENGINE', msg); },

  /* ── lifecycle ───────────────────────────── */
  start: async function () {
    if (VoiceClient.on) return true;
    try {
      VoiceClient.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }
      });
    } catch (e) {
      micProblem = 'blocked';
      VoiceClient.log('error', /NotAllowed|denied/i.test(String(e)) ? 'Mikrofon im Browser blockiert' : 'Kein Mikrofon: ' + String(e).slice(0, 60));
      updateMicButton();
      return false;
    }
    micProblem = null;
    var ctx = VoiceClient.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') {
      try { await Promise.race([ctx.resume(), new Promise(function (r) { setTimeout(r, 1000); })]); } catch (e) {}
      if (ctx.state !== 'running') { micProblem = 'needs-click'; afterGesture(function () { ctx.resume().then(function () { micProblem = null; updateMicButton(); }); }); }
    }
    try {
      var u1 = URL.createObjectURL(new Blob([VoiceClient.CAP_WORKLET], { type: 'application/javascript' }));
      var u2 = URL.createObjectURL(new Blob([VoiceClient.PLAY_WORKLET], { type: 'application/javascript' }));
      await ctx.audioWorklet.addModule(u1);
      await ctx.audioWorklet.addModule(u2);
      URL.revokeObjectURL(u1); URL.revokeObjectURL(u2);
    } catch (e) {
      VoiceClient.log('error', 'AudioWorklet nicht verfügbar — alte Sprachverarbeitung');
      VoiceClient.teardownAudio();
      return false;
    }
    var src = ctx.createMediaStreamSource(VoiceClient.stream);
    var cap = new AudioWorkletNode(ctx, 'jarvis-engine-cap', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: 'explicit' });
    var mute = ctx.createGain(); mute.gain.value = 0;
    src.connect(cap); cap.connect(mute).connect(ctx.destination);
    cap.port.onmessage = function (e) { var ws = VoiceClient.ws; if (ws && ws.readyState === 1 && VoiceClient.ready) ws.send(e.data); };
    VoiceClient.cap = cap;
    var player = new AudioWorkletNode(ctx, 'jarvis-engine-play', { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [2] });
    var gain = ctx.createGain(); gain.gain.value = outVolume();
    player.connect(gain).connect(ctx.destination);
    player.port.onmessage = function (e) { VoiceClient.onPlayer(e.data); };
    VoiceClient.player = player; VoiceClient.gain = gain;
    // the orb and the wave follow his voice
    var an = ctx.createAnalyser(); an.fftSize = 512; gain.connect(an);
    if (typeof Wave !== 'undefined') Wave.speech = an;
    VoiceClient.analyser = an;
    VoiceClient.on = true;
    Live.on = true;
    Live.ctx = ctx; Live.stream = VoiceClient.stream;        // so the old helpers (unlock, meter) stay quiet
    document.body.classList.add('live');
    $('#btnMic').classList.add('live');
    VoiceClient.connect();
    setInterval(function () { if (VoiceClient.gain) VoiceClient.gain.gain.value = outVolume(); }, 1000);
    VoiceClient.ambienceTimer = setInterval(VoiceClient.pushAmbience, 5000);
    VoiceClient.log('ok', 'Sprach-Engine verbunden — einfach reden, reinreden erlaubt');
    return true;
  },

  teardownAudio: function () {
    try { if (VoiceClient.stream) VoiceClient.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
    try { if (VoiceClient.ctx) VoiceClient.ctx.close(); } catch (e) {}
    VoiceClient.stream = VoiceClient.ctx = VoiceClient.cap = VoiceClient.player = VoiceClient.gain = null;
  },

  stop: function () {
    if (!VoiceClient.on) return;
    VoiceClient.on = false;
    VoiceClient.ready = false;
    clearTimeout(VoiceClient.reconnectTimer);
    clearInterval(VoiceClient.ambienceTimer);
    try { if (VoiceClient.ws) VoiceClient.ws.close(); } catch (e) {}
    VoiceClient.ws = null;
    VoiceClient.teardownAudio();
    Live.ctx = Live.stream = null;
    VoiceClient.mirror('IDLE');
    running = false;
  },

  connect: function () {
    if (!VoiceClient.on) return;
    var ws;
    try { ws = new WebSocket(VoiceClient.url()); } catch (e) { VoiceClient.log('error', 'WebSocket: ' + String(e).slice(0, 60)); return; }
    ws.binaryType = 'arraybuffer';
    VoiceClient.ws = ws;
    ws.onopen = function () {
      ws.send(JSON.stringify({ t: 'hello', token: TOKEN, device: 'pc', ambience: typeof Ambience !== 'undefined' ? Ambience.kind() : '' }));
      VoiceClient.openedAt = Date.now();
    };
    ws.onmessage = function (m) {
      if (m.data instanceof ArrayBuffer) { VoiceClient.onAudio(m.data); return; }
      var ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
      VoiceClient.onEvent(ev);
    };
    ws.onclose = function (e) {
      VoiceClient.ready = false;
      if (!VoiceClient.on) return;
      if (e.code === 4003) { VoiceClient.log('error', 'Engine lehnt das Token ab — Seite neu laden'); return; }
      VoiceClient.mirror('IDLE');
      VoiceClient.log('note', 'Engine getrennt — verbinde neu');
      VoiceClient.reconnectTimer = setTimeout(VoiceClient.connect, 1500);
    };
    ws.onerror = function () { /* onclose follows */ };
  },

  /* ── outgoing ────────────────────────────── */
  json: function (obj) {
    var ws = VoiceClient.ws;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  },
  send: function (message, typed, speak) { VoiceClient.json({ t: 'text', message: message, typed: !!typed, speak: speak !== false }); },
  say: function (text) {
    return new Promise(function (resolve) {
      VoiceClient.sayWaiters.push(resolve);
      VoiceClient.json({ t: 'say', text: text });
      setTimeout(resolve, 60000);           // never block the notice queue for good
    });
  },
  stopAll: function (why) { VoiceClient.json({ t: 'stop', why: why || 'Stopp' }); VoiceClient.flush(30); },
  ptt: function (on) { VoiceClient.json({ t: 'ptt', on: !!on }); },
  wakeHeard: function () { VoiceClient.json({ t: 'wake_heard' }); },
  pushAmbience: function () {
    if (typeof Ambience === 'undefined') return;
    var k = Ambience.kind() || '';
    if (k !== VoiceClient.lastAmbience) { VoiceClient.lastAmbience = k; VoiceClient.json({ t: 'ambience', kind: k }); }
  },

  /* ── incoming audio ──────────────────────── */
  onAudio: function (buf) {
    if (!VoiceClient.player || VoiceClient.pending == null) return;
    VoiceClient.player.port.postMessage({ t: 'pcm', id: VoiceClient.pending, buf: buf }, [buf]);
  },
  flush: function (ms) { if (VoiceClient.player) VoiceClient.player.port.postMessage({ t: 'flush', ms: ms || 30 }); },
  onPlayer: function (d) {
    if (d.t === 'playing') { VoiceClient.json({ t: 'playing', id: d.id }); }
    else if (d.t === 'ended') { VoiceClient.json({ t: 'ended', id: d.id }); }
    else if (d.t === 'idle') { VoiceClient.json({ t: 'idle' }); if (window.JarvisOrb) JarvisOrb.setLevel(0); }
  },

  /* ── incoming events ─────────────────────── */
  onEvent: function (ev) {
    switch (ev.t) {
      case 'ready':
        VoiceClient.ready = true;
        VoiceClient.cfg = ev.config || {};
        if (VoiceClient.player) VoiceClient.player.port.postMessage({ t: 'rate', rate: VoiceClient.cfg.out_rate || 24000 });
        VoiceClient.mirror('LISTENING');
        VoiceClient.log('note', 'bereit · VAD ' + ev.vad + (ev.partials ? ' · Partials' : '') + (ev.stt ? '' : ' · KEINE Spracherkennung'));
        break;
      case 'state': VoiceClient.mirror(ev.state, ev.why); break;
      case 'level':
        if (window.JarvisOrb && !Live.speaking) JarvisOrb.setLevel(Math.min(1, ev.p));
        var vu = $('#vuFill'); if (vu) { vu.style.width = Math.round(ev.p * 100) + '%'; vu.className = Live.armed ? 'vufill hot' : 'vufill'; }
        var ml = $('#micLvl'); if (ml) ml.style.width = Math.round(ev.p * 100) + '%';
        break;
      case 'hearing': Live.armed = true; setVoiceState('HEARING', 'hot'); break;
      case 'listening': Live.armed = false; Live.sending = false; setVoiceState('LISTENING', 'hot'); break;
      case 'transcribing': Live.armed = false; Live.sending = true; setVoiceState('TRANSCRIBING', 'hot'); break;
      case 'partial': setText('#voiceState', 'HEARING · ' + ev.text.slice(-40)); break;
      case 'final': Live.sending = false; VoiceClient.log('note', 'heard: "' + ev.text + '"'); break;
      case 'run_start':
        running = true; streamSpeak = false; answerText = ''; typedRun = !!ev.typed;
        document.body.classList.add('running');
        if (graph) graph.setActivity(1);
        if (!ev.typed) bubble('me', ev.message);
        answerBubble = bubble('jarvis', ''); answerBubble.classList.add('thinking');
        $('#toneNow').textContent = window.__persona || '—';
        log('run', 'RUN', ev.message.slice(0, 70));
        VoiceClient.t0 = performance.now();
        break;
      case 'run':
        if (ev.ev && ev.ev.t === 'complete') {
          running = false; document.body.classList.remove('running');
          if (graph) graph.setActivity(0);
          if (answerBubble) answerBubble.classList.remove('thinking');
          $('#sLatency').textContent = Math.round(performance.now() - (VoiceClient.t0 || performance.now())) + 'ms';
        }
        if (ev.ev && ev.ev.t === 'error') { running = false; document.body.classList.remove('running'); if (answerBubble) answerBubble.classList.remove('thinking'); }
        if (ev.ev) handleEvent(ev.ev);
        break;
      case 'audio_begin':
        VoiceClient.pending = ev.id;
        if (!Live.speaking) { Live.speaking = true; setVoiceState('SPEAKING', 'hot'); duckMusic(true); }
        if (window.JarvisOrb) JarvisOrb.setState('SPEAKING');
        break;
      case 'audio_end':
        if (VoiceClient.player) VoiceClient.player.port.postMessage({ t: 'pcm', id: ev.id, buf: new ArrayBuffer(0), end: true });
        break;
      // The answer comes from the room's Echo or Sonos: music on another device of the account
      // (Spotify Connect on the soundbar) must dip just the same as when this device speaks.
      case 'room_audio':
        if (!Live.speaking) { Live.speaking = true; setVoiceState('SPEAKING', 'hot'); }
        duckMusic(true);
        break;
      case 'gain': if (VoiceClient.player) VoiceClient.player.port.postMessage({ t: 'gain', value: ev.value, ms: ev.ms }); break;
      case 'flush': VoiceClient.flush(30); VoiceClient.pending = null; break;
      case 'speak_done':
        Live.speaking = false; Live.spokeAt = performance.now(); duckMusic(false);
        if (!ev.interrupted) Wake.followUp(answerText);
        var w = VoiceClient.sayWaiters; VoiceClient.sayWaiters = []; w.forEach(function (f) { f(); });
        break;
      case 'cue': VoiceClient.thinkingCue(); break;
      case 'chime': if (typeof wakeChime === 'function') wakeChime(); break;
      case 'window': Wake.until = performance.now() + Math.max(0, (ev.until * 1000) - Date.now()); updateMicButton(); break;
      case 'log': VoiceClient.log('note', ev.text); break;
    }
  },

  /* the server's state in the HUD's words */
  mirror: function (state, why) {
    VoiceClient.state = state;
    switch (state) {
      case 'IDLE': Live.armed = Live.sending = Live.speaking = false; running = false; setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : ''); break;
      case 'LISTENING': Live.armed = Live.sending = Live.speaking = false; running = false; document.body.classList.remove('running'); if (answerBubble) answerBubble.classList.remove('thinking'); setVoiceState('LISTENING', 'hot'); break;
      case 'THINKING': running = true; Live.armed = Live.sending = false; Live.speaking = false; setVoiceState('THINKING', 'hot'); break;
      case 'SPEAKING': running = false; document.body.classList.remove('running'); Live.speaking = true; setVoiceState('SPEAKING', 'hot'); break;
      case 'INTERRUPTED': Live.speaking = false; Live.armed = true; setVoiceState('PAUSED · höre', 'hot'); break;
      case 'RESUMING': setVoiceState('SPEAKING', 'hot'); break;
    }
    if (why) VoiceClient.log('note', state + ' ← ' + why);
    updateMicButton();
  },

  /* a soft two-note "thinking" cue while the model has not said a word yet */
  thinkingCue: function () {
    try {
      var ctx = VoiceClient.ctx; if (!ctx || ctx.state !== 'running') return;
      var g = ctx.createGain(); g.gain.value = 0.0001; g.connect(ctx.destination);
      [520, 660].forEach(function (f, i) {
        var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = f; o.connect(g);
        var t = ctx.currentTime + i * 0.16;
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.05 * outVolume(), t + 0.03); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
        o.start(t); o.stop(t + 0.15);
      });
    } catch (e) { /* no sound is fine */ }
  }
};

/* Desktop card — while this tab is hidden, the server shows a small always-on-top
   window (jarvis/voice/overlay.py): orb, what Jarvis is doing, the last line, Stop
   and Mic. Browsers open their own picture-in-picture only after a click, and Opera
   ignores the automatic route, so the card lives outside the browser.

   This file only reports: is the tab visible, and what does the Live button show.
   On every change at once, otherwise every few seconds as a sign of life — a hidden
   tab gets no animation frames, so the audio ticker calls Pip.tick (Live.startTicker). */
(function () {
  if (window.__remote) return;          // a phone or iPad has no desktop of this PC
  var last = '', lastAt = 0;
  // each tab reports under its own id, so a forgotten hidden tab cannot keep the card up
  var tab = Math.random().toString(36).slice(2, 12);

  /* While he talks: the tail of his answer. Otherwise: what you said last. */
  function line(st) {
    var text = '';
    if ((st === 'speaking' || st === 'busy') && typeof answerText === 'string' && answerText) {
      text = stripTones(answerText);
    } else {
      var mine = document.querySelectorAll('#transcript .bubble.me');
      if (mine.length) text = '„' + mine[mine.length - 1].textContent.trim() + '“';
    }
    text = text.replace(/\s+/g, ' ').trim();
    return text.length > 90 ? '… ' + text.slice(-88).replace(/^\S*\s/, '') : text;
  }

  function snapshot(visible) {
    var btn = document.getElementById('btnMic'), label = document.getElementById('micLabel');
    var st = (btn && btn.dataset.state) || 'off';
    // the tone tag of the answer ([dry], [amused] …) sets the panda's face
    var tone = typeof answerText === 'string' && typeof lastTone === 'function' ? lastTone(answerText) || '' : '';
    return { tab: tab, visible: visible, st: st, detail: label ? label.textContent : '', line: line(st), tone: tone };
  }

  function send(force, visible, gone) {
    var snap = snapshot(visible === undefined ? !document.hidden : visible);
    if (gone) snap.gone = true;
    var body = JSON.stringify(snap);
    var now = performance.now();
    if (!force && body === last && now - lastAt < 4000) return;
    last = body; lastAt = now;
    if (window.__remote) return;   // the desktop card is the PC's; the server refuses it from a phone
    fetch('/api/hud', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
                        body: body, keepalive: true }).catch(function () {});
  }

  window.Pip = { tick: function () { if (document.hidden) send(false); } };
  document.addEventListener('visibilitychange', function () { send(true); });
  // Tab closed or reloaded: take the card away at once instead of waiting for it to go stale.
  window.addEventListener('pagehide', function () { send(true, false, true); });
  setInterval(function () { send(false); }, 1000);
  send(true);
})();

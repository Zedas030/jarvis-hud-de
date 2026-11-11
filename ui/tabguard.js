/* One HUD tab per browser. After a reboot the browser restores the old Jarvis tab and the server
   used to open another one on top, so five tabs piled up. server.py now opens none while a tab
   reports itself; this file handles the ones that slip through (a tab the browser only loads
   when it is clicked, a second start by hand).

   Every new tab says hello on a BroadcastChannel; the others answer. Of two tabs the one you
   look at stays, between two equal ones the older. The other closes itself — a tab the server
   opened may do that — or, where the browser refuses, turns into tab-doppelt.html: a page without
   microphone, voice or timers. ?zweit=1 opens a second tab on purpose. */
(function () {
  if (!('BroadcastChannel' in window) || /[?&]zweit=1\b/.test(location.search)) return;
  var me = { id: Math.random().toString(36).slice(2, 10), born: Date.now() };
  var ch = new BroadcastChannel('jarvis-hud-tab');
  var gone = false;

  function info(t) {
    return { t: t, id: me.id, born: me.born, visible: document.visibilityState === 'visible' };
  }

  function beats(a, b) {
    if (a.visible !== b.visible) return a.visible;
    return a.born < b.born || (a.born === b.born && a.id < b.id);
  }

  function retire() {
    if (gone) return;
    gone = true;
    try { ch.close(); } catch (e) { /* already closed */ }
    window.close();
    setTimeout(function () { location.replace('/tab-doppelt.html'); }, 400);
  }

  ch.onmessage = function (e) {
    var m = e.data || {};
    if (gone || !m.id || m.id === me.id) return;
    if (m.t === 'hello') ch.postMessage(info('here'));
    if ((m.t === 'hello' || m.t === 'here') && beats(m, info())) retire();
  };
  ch.postMessage(info('hello'));
})();

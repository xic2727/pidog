/* PiDog Local Web Console — landscape controller SPA
 * Layout: [ head-dpad | video | squares + circles ]
 *         [ move-dpad | status| (lights hidden in v1.1) ]
 *         [--- bottombar: status + voice switch ---]
 */
(() => {
  'use strict';

  // --- State ------------------------------------------------------------
  const state = {
    online:       false,
    posture:      null,
    lastAction:   null,
    lastLight:    null,
    lastHead:     null,
    head:         { yaw: 0, roll: 0, pitch: 0 },
    voice:        false,
    voicePid:     null,
    uptime:       0,
    requestCount: 0,
    inflight:     new Set(),
    holding:      null,
    ws:           null,
    wsBackoffMs:  1000,
    videoOk:      false,
  };

  const $  = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // --- Helpers ----------------------------------------------------------
  function log(msg, kind = '') {
    // (logging is intentionally not in the layout, but we keep it for debugging)
    if (window.console) console.debug(`[${new Date().toLocaleTimeString()}] ${msg}`);
  }
  function toast(msg, kind = '') {
    const t = $('#toast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'toast show ' + kind;
    clearTimeout(toast._h);
    toast._h = setTimeout(() => { t.className = 'toast ' + kind; }, 1800);
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { 'content-type': 'application/json' },
      ...opts,
    });
    let body = null;
    try { body = await r.json(); } catch { /* empty */ }
    if (!r.ok || (body && body.ok === false)) {
      const err = (body && body.error) || `HTTP ${r.status}`;
      const code = (body && body.code) || 'HTTP_ERROR';
      throw Object.assign(new Error(err), { code, status: r.status });
    }
    return body && body.data;
  }
  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^\w-]/g, c => `\\${c}`);
  }

  // --- Boot -------------------------------------------------------------
  async function boot() {
    bindTopbar();
    bindDpad();
    bindActionButtons();
    bindSoundButtons();
    bindHeadHome();
    bindVoiceSwitch();
    bindVideoFallback();
    await initialStatus();
    connectWS();
  }

  // --- Top bar / global -------------------------------------------------
  function bindTopbar() {
    $('#btn-url').addEventListener('click', async () => {
      const u = window.location.origin + '/';
      try { await navigator.clipboard.writeText(u); toast('已复制 URL', 'ok'); }
      catch { toast(u, ''); }
    });
    $('#btn-stop-all').addEventListener('click', onStopAll);
  }

  async function onStopAll() {
    if (state.holding) {
      try {
        await api('/api/action/release', { method: 'POST', body: JSON.stringify({ name: state.holding }) });
        clearHoldingMark();
        state.holding = null;
      } catch (e) { toast(`释放失败: ${e.message}`, 'err'); }
    }
    try { await api('/api/stop', { method: 'POST' }); } catch (e) { /* swallow */ }
    try { await api('/api/light/off', { method: 'POST' }); toast('已停所有动作', 'ok'); }
    catch (e) { /* swallow */ }
  }

  // --- D-pad: head + movement -------------------------------------------
  function bindDpad() {
    // Head nudges: single press (one nudge per click).
    for (const btn of $$('#dpad-head .dpad-btn')) {
      btn.addEventListener('click', () => onHeadNudge(btn.dataset.nudge, parseFloat(btn.dataset.delta)));
    }
    // Movement: press-and-hold for continuous motion.
    bindHoldableMove($$('#dpad-move [data-action]'));
  }

  /**
   * Press-and-hold for the four movement D-pad buttons.
   * - pointerdown → trigger continuous move worker via /api/move
   * - pointerup / pointercancel / pointerleave → trigger /api/stop
   */
  function bindHoldableMove(buttons) {
    for (const btn of buttons) {
      const name = btn.dataset.action;
      let isMoving = false;

      const startMove = async (e) => {
        e.preventDefault();
        if (isMoving) return;
        if (state.voice) { toast('语音模式已开启, 硬件动作暂停', 'err'); return; }

        if (e.pointerId !== undefined && btn.setPointerCapture) {
          try { btn.setPointerCapture(e.pointerId); } catch {}
        }
        btn.classList.add('pressing');
        isMoving = true;

        try {
          await api('/api/move', {
            method: 'POST',
            body: JSON.stringify({ name, speed: 98 }),
          });
        } catch (err) {
          btn.classList.remove('pressing');
          isMoving = false;
          if (err.code === 'VOICE_MODE_ACTIVE') {
            toast('语音模式已开启, 硬件动作暂停', 'err');
          } else {
            toast(`移动失败: ${err.message}`, 'err');
          }
        }
      };

      const stopMove = async (e) => {
        e.preventDefault();
        btn.classList.remove('pressing');
        if (!isMoving) return;
        isMoving = false;
        try {
          await api('/api/stop', { method: 'POST' });
        } catch { /* swallow — best effort */ }
      };

      btn.addEventListener('pointerdown',   startMove);
      btn.addEventListener('pointerup',     stopMove);
      btn.addEventListener('pointercancel', stopMove);
      btn.addEventListener('pointerleave',  stopMove);
    }
  }

  async function onHeadNudge(axis, delta) {
    try {
      const data = await api('/api/head/nudge', {
        method: 'POST', body: JSON.stringify({ axis, delta, speed: 50 }),
      });
      state.head.yaw   = data.yaw   ?? state.head.yaw;
      state.head.roll  = data.roll  ?? state.head.roll;
      state.head.pitch = data.pitch ?? state.head.pitch;
      updateHeadCoord();
      log(`head ${axis}${delta>0?'+':''}${delta}° → Y${state.head.yaw} P${state.head.pitch}`, 'ok');
    } catch (e) {
      toast(`头部位姿失败: ${e.message}`, 'err');
    }
  }

  function bindHeadHome() {
    const onHome = async () => {
      try {
        const data = await api('/api/head/home', { method: 'POST' });
        state.head.yaw = data.yaw; state.head.roll = data.roll; state.head.pitch = data.pitch;
        updateHeadCoord();
        toast('头部回中', 'ok');
      } catch (e) { toast(`回中失败: ${e.message}`, 'err'); }
    };
    const btnCenter = $('#btn-head-home');
    if (btnCenter) btnCenter.addEventListener('click', onHome);
    const btnTop = $('#btn-head-home-top');
    if (btnTop) btnTop.addEventListener('click', onHome);
  }
  function updateHeadCoord() {
    const el = $('#head-coord');
    if (el) el.textContent = `Y ${state.head.yaw.toFixed(0)}°  P ${state.head.pitch.toFixed(0)}°`;
    const st = $('#st-head');
    if (st) st.textContent = `${state.head.yaw.toFixed(0)}/${state.head.pitch.toFixed(0)}`;
  }

  // --- Action buttons (squares + circles) ------------------------------
  function bindActionButtons() {
    for (const btn of $$('#grid-squares [data-action], #grid-circles [data-action]')) {
      const name = btn.dataset.action;
      const hold = btn.dataset.hold === '1';
      btn.addEventListener('click', () => onAction(name, hold));
    }
  }

  async function onAction(name, hold) {
    if (state.inflight.has(name)) return;
    state.inflight.add(name);
    const btn = $(`button[data-action="${cssEscape(name)}"]`);
    if (btn) btn.disabled = true;

    try {
      if (hold && state.holding && state.holding !== name) {
        await releaseHold(state.holding, /*silent*/ true);
      }
      const data = await api('/api/action', {
        method: 'POST',
        body: JSON.stringify({ name, speed: 70, hold: !!hold }),
      });
      log(`action ${name}${hold?'(hold)':''} ok`, 'ok');
      // Surface sound playback failures (missing player, SDL/PulseAudio
      // backend issues in the headless daemon) instead of swallowing them.
      if (data && data.sound && data.sound.ok === false) {
        const detail = data.sound.detail || data.sound.player || 'unknown';
        toast(`声音播放失败 (${detail})`, 'warn');
      }
      if (hold) { state.holding = name; markHolding(name); }
      else { setTimeout(() => { state.inflight.delete(name); if (btn) btn.disabled = false; }, 600); return; }
    } catch (e) {
      if (e.code === 'VOICE_MODE_ACTIVE') {
        toast('语音模式已开启, 硬件动作暂停', 'err');
      } else {
        toast(`动作失败: ${e.message}`, 'err');
      }
    }
    state.inflight.delete(name);
    if (btn) btn.disabled = false;
  }

  async function releaseHold(name, silent = false) {
    try {
      await api('/api/action/release', { method: 'POST', body: JSON.stringify({ name }) });
      if (!silent) toast(`释放 ${name}`, 'ok');
    } catch (e) {
      if (!silent) toast(`释放失败: ${e.message}`, 'err');
    } finally {
      if (state.holding === name) { state.holding = null; clearHoldingMark(); }
    }
  }

  function markHolding(name) {
    clearHoldingMark();
    const btn = $(`button[data-action="${cssEscape(name)}"]`);
    if (btn) btn.classList.add('holding');
  }
  function clearHoldingMark() {
    const btn = document.querySelector('button.holding');
    if (btn) btn.classList.remove('holding');
  }

  // --- Sound buttons (birthday song / greeting) ----------------------------
  function bindSoundButtons() {
    for (const btn of $$('[data-sound]')) {
      btn.addEventListener('click', () => onSound(btn.dataset.sound));
    }
  }

  async function onSound(name) {
    try {
      await api('/api/sound', {
        method: 'POST',
        body: JSON.stringify({ sound: name }),
      });
      toast('播放成功', 'ok');
    } catch (e) {
      toast(`播放失败: ${e.message}`, 'err');
    }
  }

  // --- Voice switch -----------------------------------------------------
  function bindVoiceSwitch() {
    const toggle = $('#voice-toggle');
    toggle.addEventListener('change', async (e) => {
      const on = e.target.checked;
      toggle.disabled = true;
      try {
        const data = await api(on ? '/api/voice/on' : '/api/voice/off', { method: 'POST' });
        applyVoice(data);
        toast(`语音模式 ${on ? '开' : '关'}`, on ? 'ok' : '');
      } catch (err) {
        // Revert the UI
        toggle.checked = !on;
        toast(`语音模式切换失败: ${err.message}`, 'err');
      } finally {
        toggle.disabled = false;
      }
    });
  }

  function applyVoice(data) {
    state.voice    = !!data.voice_mode;
    state.voicePid = data.voice_pid || null;
    $('#voice-toggle').checked = state.voice;
    const pill = $('#voice-status-pill');
    pill.textContent = state.voice ? '语音 开' : '语音 关';
    pill.className   = 'pill ' + (state.voice ? 'pill-on' : 'pill-off');
    $('#bb-msg').textContent = state.voice
      ? `语音助手运行中 (pid ${state.voicePid ?? '?'})`
      : '就绪';
    // Disable manual hardware controls while voice is on.
    for (const sel of ['[data-action]', '#dpad-head .dpad-btn', '#btn-head-home']) {
      for (const b of $$(sel)) {
        if (b === $('#voice-toggle')) continue;
        b.disabled = state.voice;
      }
    }
  }

  // --- Video ------------------------------------------------------------
  // The server can either:
  //   (a) Same-origin proxy: `/api/camera/stream` (preferred — sidesteps
  //       cross-port <img> quirks and mDNS resolution failures on phones), or
  //   (b) Direct URL with `{host}` placeholder (legacy, replaced client-side
  //       with window.location.hostname).
  // We accept both shapes and build the final URL accordingly.
  const DEFAULT_MJPEG_URL = 'http://{host}:9000/mjpg';
  let mjpegTemplate = DEFAULT_MJPEG_URL;  // shared between bind + reconnect

  async function resolveMjpegTemplate() {
    try {
      const info = await api('/api/camera/info');
      const tpl = info && info.mjpeg_url_template;
      if (typeof tpl === 'string' && tpl.length > 0) return tpl;
    } catch { /* fall through */ }
    return DEFAULT_MJPEG_URL;
  }

  function buildMjpegUrl(template, cacheBust = true) {
    // Same-origin proxy: starts with '/' → resolve against current origin
    // and skip host substitution (would otherwise corrupt the path).
    if (template.startsWith('/')) {
      const base = template.replace(/[?&]t=\d+/, '');
      return base + (cacheBust
        ? (base.includes('?') ? '&' : '?') + `t=${Date.now()}`
        : '');
    }
    // Direct URL with {host} placeholder.
    const tpl = template.replace('{host}', window.location.hostname)
                        .replace(/[?&]t=\d+/, '');
    return tpl + (cacheBust
      ? (tpl.includes('?') ? '&' : '?') + `t=${Date.now()}`
      : '');
  }

  function bindVideoFallback() {
    const img = $('#mjpeg');
    const section = $('#video-section');
    const fallback = $('#video-fallback');

    const tryLoad = () => { img.src = buildMjpegUrl(mjpegTemplate); };

    img.addEventListener('load',  () => {
      state.videoOk = true;
      section.classList.remove('no-stream');
      hideVideoRetry();
    });
    img.addEventListener('error', () => {
      state.videoOk = false;
      section.classList.add('no-stream');
      showVideoRetry();
    });

    // Pull the configured URL template, then attempt the stream.
    resolveMjpegTemplate().then((tpl) => {
      mjpegTemplate = tpl;
      tryLoad();
    });

    // Auto-retry every 5s while the stream is broken.
    setInterval(() => { if (!state.videoOk) tryLoad(); }, 5000);

    // Tap-to-retry on the fallback overlay (covers the case where vilib
    // was offline at boot and the user wants to retry without waiting 5s).
    if (fallback) {
      fallback.addEventListener('click', (ev) => {
        ev.preventDefault();
        tryLoad();
      });
      fallback.style.cursor = 'pointer';
    }
  }

  function showVideoRetry() {
    const fb = $('#video-fallback');
    if (!fb) return;
    if (!fb.querySelector('.fallback-retry')) {
      const hint = document.createElement('small');
      hint.className = 'fallback-retry';
      hint.textContent = '点击此处立即重试';
      fb.appendChild(hint);
    }
  }
  function hideVideoRetry() {
    const fb = $('#video-fallback');
    if (!fb) return;
    const hint = fb.querySelector('.fallback-retry');
    if (hint) hint.remove();
  }

  // When the daemon reconnects, the upstream service may also have bounced
  // vilib — proactively refresh the video URL to recover from silent stream
  // freezes (where `<img>.error` doesn't fire but frames go stale).
  function refreshVideoOnReconnect() {
    const img = $('#mjpeg');
    if (!img) return;
    const tpl = mjpegTemplate.replace('{host}', window.location.hostname);
    img.src = tpl + (tpl.includes('?') ? '&' : '?') + `t=${Date.now()}`;
  }

  // --- WebSocket status -------------------------------------------------
  function connectWS() {
    if (state.ws) { try { state.ws.close(); } catch {} }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/status`);
    state.ws = ws;
    ws.addEventListener('open',  () => { state.wsBackoffMs = 1000; setOnline(true); });
    ws.addEventListener('close', () => {
      setOnline(false);
      setTimeout(connectWS, state.wsBackoffMs);
      state.wsBackoffMs = Math.min(state.wsBackoffMs * 2, 15000);
    });
    ws.addEventListener('error', () => { /* close handles reconnect */ });
    ws.addEventListener('message', (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.op === 'ping') return;
      applyStatus(msg);
    });
  }
  function setOnline(b) {
    state.online = b;
    $('#conn-dot').className  = 'dot ' + (b ? 'dot-on' : 'dot-off');
    $('#conn-text').textContent = b ? '在线' : '离线';
    // Recover from a possibly-frozen video stream after the daemon comes back.
    if (b) refreshVideoOnReconnect();
  }
  function applyStatus(s) {
    if (typeof s.uptime_s === 'number')     state.uptime = s.uptime_s;
    if (typeof s.request_count === 'number')state.requestCount = s.request_count;
    if (s.current_posture !== undefined)    state.posture = s.current_posture;
    if (s.last_action) state.lastAction = s.last_action;
    if (s.last_light)  state.lastLight  = s.last_light;
    if (s.last_head)   state.lastHead   = s.last_head;
    if (Array.isArray(s.head_state) && s.head_state.length === 3) {
      state.head.yaw = s.head_state[0];
      state.head.roll = s.head_state[1];
      state.head.pitch = s.head_state[2];
    }
    if (typeof s.voice_mode === 'boolean') applyVoice({ voice_mode: s.voice_mode, voice_pid: s.voice_pid });
    $('#st-posture').textContent = state.posture || '—';
    $('#st-count').textContent   = String(state.requestCount);
    const lightLabel = state.lastLight && state.lastLight.mode ? state.lastLight.mode : 'off';
    $('#st-light').textContent   = lightLabel;
    updateHeadCoord();
  }
  async function initialStatus() {
    try {
      const s = await api('/api/daemon/status');
      if (s && s.daemon) applyStatus(s);
      setOnline(s && s.daemon === 'up');
    } catch { setOnline(false); }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();

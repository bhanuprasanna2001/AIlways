"""Streamlit UI for AIlways — Real-time Transcription & Claim Verification.

**Please copy this and paste it to backend root to run the Streamlit app.**

Run with: streamlit run streamlit_app.py

Pipeline:
  Browser Mic → AudioWorklet (VAD) → WebSocket → Backend → DeepGram Nova 3
    → Groq claim detection → RAG verification → real-time alerts

Audio improvements over v1:
  - AudioWorklet instead of ScriptProcessorNode (runs on audio render thread,
    zero dropped frames, guaranteed timing)
  - Energy-based VAD with hangover (only sends speech, keepalive in silence)
  - Live volume meter + speaking/silent indicator
  - Auto-reconnect on WebSocket drop (3 attempts, exponential backoff)
  - DOM segment cap (200 transcript, 50 claims) for long sessions
  - Mic disconnect detection + tab visibility handler
"""

from __future__ import annotations

import json
import requests
import streamlit as st
import streamlit.components.v1 as components


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = "http://localhost:8080"
WS_BASE = "ws://localhost:8080"


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _init_state() -> None:
    defaults = {
        "authenticated": False,
        "session_cookies": {},
        "session_id": "",
        "csrf_token": "",
        "user_name": "",
        "vaults": [],
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _login(email: str, password: str) -> bool:
    try:
        resp = requests.post(
            f"{API_BASE}/auth/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            cookies = dict(resp.cookies)
            st.session_state.session_cookies = cookies
            st.session_state.session_id = cookies.get("session_id", "")
            data = resp.json()
            st.session_state.csrf_token = data.get("csrf_token", "")
            st.session_state.user_name = data.get("user", {}).get("name", "User")
            st.session_state.authenticated = True
            return True
        else:
            st.error(f"Login failed: {resp.json().get('detail', 'Unknown error')}")
            return False
    except requests.ConnectionError:
        st.error("Cannot connect to backend — is it running on localhost:8080?")
        return False


def _api_get(path: str) -> dict | list | None:
    try:
        resp = requests.get(
            f"{API_BASE}{path}",
            cookies=st.session_state.session_cookies,
            headers={"X-CSRF-Token": st.session_state.csrf_token},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            st.session_state.authenticated = False
            st.warning("Session expired — please log in again.")
            return None
        else:
            st.error(f"API error: {resp.status_code}")
            return None
    except requests.ConnectionError:
        st.error("Lost connection to backend.")
        return None


# ---------------------------------------------------------------------------
# AudioWorklet processor source (VAD + int16 conversion)
# ---------------------------------------------------------------------------

_WORKLET_PROCESSOR_JS = """
class VADProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._bufSize = 4096;
    this._buf = new Float32Array(this._bufSize);
    this._pos = 0;
    this._speaking = false;
    this._hangoverCount = 0;
    this._silenceChunks = 0;
    this._threshold = 0.01;

    var chunkDur = this._bufSize / sampleRate;
    this._hangoverMax = Math.ceil(1.0 / chunkDur);
    this._keepaliveEvery = Math.ceil(5.0 / chunkDur);
  }

  process(inputs) {
    var inp = inputs[0];
    if (!inp || !inp[0]) return true;
    var data = inp[0];
    for (var i = 0; i < data.length; i++) {
      this._buf[this._pos++] = data[i];
      if (this._pos >= this._bufSize) {
        this._flush();
      }
    }
    return true;
  }

  _flush() {
    var sum = 0;
    for (var i = 0; i < this._bufSize; i++) {
      sum += this._buf[i] * this._buf[i];
    }
    var rms = Math.sqrt(sum / this._bufSize);

    if (rms >= this._threshold) {
      this._speaking = true;
      this._hangoverCount = 0;
      this._silenceChunks = 0;
    } else {
      this._hangoverCount++;
      this._silenceChunks++;
      if (this._hangoverCount >= this._hangoverMax) {
        this._speaking = false;
      }
    }

    var isKeepalive = !this._speaking
      && this._silenceChunks > 0
      && (this._silenceChunks % this._keepaliveEvery === 0);
    var shouldSend = this._speaking || isKeepalive;

    if (shouldSend) {
      var int16 = new Int16Array(this._bufSize);
      for (var j = 0; j < this._bufSize; j++) {
        var s = Math.max(-1, Math.min(1, this._buf[j]));
        int16[j] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      this.port.postMessage(
        { audio: int16.buffer, rms: rms, speaking: this._speaking },
        [int16.buffer]
      );
    } else {
      this.port.postMessage({ audio: null, rms: rms, speaking: this._speaking });
    }
    this._pos = 0;
  }
}
registerProcessor('vad-processor', VADProcessor);
"""


# ---------------------------------------------------------------------------
# Live component HTML template
#
# Placeholders replaced at runtime:
#   __WORKLET_CODE__  →  JSON-encoded worklet JS string
#   __WS_URL__        →  WebSocket URL with session_id
# ---------------------------------------------------------------------------

_LIVE_COMPONENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  :root {
    --bg: #0e1117;
    --surface: #1a1d24;
    --surface2: #262730;
    --border: #333;
    --text: #e0e0e0;
    --text-dim: #888;
    --accent: #4da6ff;
    --green: #2ecc71;
    --red: #e74c3c;
    --orange: #f39c12;
    --red-glow: rgba(231, 76, 60, 0.3);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    overflow: hidden;
  }

  /* ---- Header ---- */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .status-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: #555;
    transition: background 0.3s;
    flex-shrink: 0;
  }
  .status-dot.connected { background: var(--green); }
  .status-dot.recording { background: var(--red); animation: pulse 1.2s infinite; }
  .status-dot.error { background: var(--red); }
  .status-dot.connecting { background: var(--orange); animation: pulse 0.8s infinite; }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .status-text {
    font-size: 13px;
    color: var(--text-dim);
    min-width: 100px;
  }

  /* Volume meter */
  .volume-meter {
    width: 80px;
    height: 6px;
    background: var(--surface2);
    border-radius: 3px;
    overflow: hidden;
    flex-shrink: 0;
    display: none;
  }
  .volume-fill {
    height: 100%;
    width: 0%;
    border-radius: 3px;
    transition: width 0.08s linear, background 0.3s;
    background: var(--text-dim);
  }
  .volume-fill.speaking { background: var(--green); }
  .volume-fill.silent   { background: var(--text-dim); }

  /* VAD badge */
  .vad-badge {
    display: none;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    transition: all 0.3s;
    background: var(--surface2);
    color: var(--text-dim);
  }
  .vad-badge.speaking { background: rgba(46, 204, 113, 0.2); color: var(--green); }
  .vad-badge.silent   { background: var(--surface2); color: var(--text-dim); }

  /* Header right */
  .header-right {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .timer {
    font-size: 14px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--text-dim);
    min-width: 48px;
    text-align: right;
  }
  .timer.active { color: var(--text); }
  .controls { display: flex; gap: 8px; }
  .btn {
    padding: 7px 16px;
    border: none;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    color: #fff;
  }
  .btn:disabled { opacity: 0.35; cursor: not-allowed; }
  .btn-start { background: var(--green); }
  .btn-start:hover:not(:disabled) { background: #27ae60; }
  .btn-stop { background: var(--red); }
  .btn-stop:hover:not(:disabled) { background: #c0392b; }
  .btn-clear { background: var(--surface2); border: 1px solid var(--border); color: var(--text-dim); }

  /* ---- Alert Banner ---- */
  .alert-banner {
    display: none;
    padding: 10px 20px;
    background: var(--red);
    color: #fff;
    font-weight: 600;
    font-size: 14px;
    text-align: center;
  }
  .alert-banner.visible {
    display: block;
    animation: flash 0.6s ease-in-out 3;
  }
  @keyframes flash {
    0%, 100% { background: var(--red); }
    50% { background: #c0392b; box-shadow: 0 0 20px var(--red-glow); }
  }

  /* ---- Content ---- */
  .content { display: flex; height: calc(100vh - 56px); }

  /* ---- Transcript Panel ---- */
  .transcript-panel {
    flex: 3;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
  }
  .panel-header {
    padding: 12px 20px;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .panel-header .badge {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--surface2);
    color: var(--text-dim);
  }
  .transcript-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
  }
  .segment {
    margin-bottom: 12px;
    padding: 8px 12px;
    border-radius: 6px;
    background: var(--surface);
    border-left: 3px solid var(--accent);
    animation: fadeIn 0.3s ease;
  }
  .segment.interim { opacity: 0.5; border-left-color: var(--text-dim); }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .segment-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
    font-size: 12px;
    color: var(--text-dim);
  }
  .speaker-badge {
    font-size: 11px;
    font-weight: 700;
    padding: 1px 8px;
    border-radius: 10px;
    color: #fff;
  }
  .segment-text { font-size: 14px; line-height: 1.5; }

  /* ---- Claims Panel ---- */
  .claims-panel {
    flex: 2;
    display: flex;
    flex-direction: column;
  }
  .claims-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
  }
  .claim-card {
    margin-bottom: 12px;
    padding: 12px;
    border-radius: 8px;
    background: var(--surface);
    border: 1px solid var(--border);
    animation: fadeIn 0.3s ease;
  }
  .claim-card.contradicted {
    border-color: var(--red);
    background: rgba(231, 76, 60, 0.08);
    animation: fadeIn 0.3s ease, alertPulse 1s ease 1;
  }
  .claim-card.supported    { border-color: var(--green); }
  .claim-card.unverifiable { border-color: var(--orange); }
  .claim-card.verifying    { border-color: var(--accent); opacity: 0.7; }
  @keyframes alertPulse {
    0%, 100% { box-shadow: none; }
    50% { box-shadow: 0 0 16px var(--red-glow); }
  }
  .verdict-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 10px;
    border-radius: 10px;
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  .verdict-badge.contradicted { background: var(--red);    color: #fff; }
  .verdict-badge.supported    { background: var(--green);  color: #fff; }
  .verdict-badge.unverifiable { background: var(--orange); color: #fff; }
  .verdict-badge.verifying    { background: var(--accent); color: #fff; }
  .claim-text { font-size: 13px; line-height: 1.5; margin-bottom: 6px; }
  .claim-speaker { font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
  .claim-explanation {
    font-size: 12px; color: var(--text-dim);
    margin-top: 6px; padding-top: 6px;
    border-top: 1px solid var(--border);
    line-height: 1.5;
  }
  .claim-evidence {
    font-size: 11px; color: var(--text-dim);
    margin-top: 4px; padding: 6px 8px;
    background: var(--surface2); border-radius: 4px;
    font-style: italic;
  }
  .confidence-bar {
    height: 3px; border-radius: 2px;
    background: var(--surface2); margin-top: 6px; overflow: hidden;
  }
  .confidence-fill {
    height: 100%; border-radius: 2px;
    transition: width 0.5s ease;
  }

  /* ---- Counters ---- */
  .counter-row {
    display: flex; gap: 12px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .counter { font-size: 12px; display: flex; align-items: center; gap: 4px; }
  .counter .num { font-weight: 700; font-size: 14px; }

  /* ---- Empty state ---- */
  .empty-state {
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100%; color: var(--text-dim); gap: 8px;
  }
  .empty-state .icon { font-size: 40px; opacity: 0.3; }

  /* ---- Scrollbar ---- */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<!-- Alert Banner -->
<div class="alert-banner" id="alertBanner">
  ⚠️ CONTRADICTED CLAIM — <span id="alertText"></span>
</div>

<!-- Header -->
<div class="header">
  <div class="header-left">
    <div class="status-dot" id="statusDot"></div>
    <span class="status-text" id="statusText">Ready</span>
    <div class="volume-meter" id="volumeMeter">
      <div class="volume-fill" id="volumeFill"></div>
    </div>
    <span class="vad-badge" id="vadBadge">silent</span>
  </div>
  <div class="header-right">
    <span class="timer" id="timer">00:00</span>
    <div class="controls">
      <button class="btn btn-start" id="btnStart" onclick="startRecording()">▶ Start</button>
      <button class="btn btn-stop" id="btnStop" onclick="stopRecording()" disabled>◼ Stop</button>
      <button class="btn btn-clear" onclick="clearAll()">Clear</button>
    </div>
  </div>
</div>

<!-- Content -->
<div class="content">
  <div class="transcript-panel">
    <div class="panel-header">
      LIVE TRANSCRIPT
      <span class="badge" id="speakerCount">0 speakers</span>
    </div>
    <div class="transcript-scroll" id="transcriptScroll">
      <div class="empty-state" id="transcriptEmpty">
        <span class="icon">🎙️</span>
        <span>Click <b>Start</b> to begin live transcription</span>
        <span style="font-size:11px">Your microphone audio is transcribed in real-time with speaker diarization</span>
      </div>
    </div>
  </div>

  <div class="claims-panel">
    <div class="panel-header">
      ⚡ CLAIM VERIFICATION
      <span class="badge" id="claimCount">0 claims</span>
    </div>
    <div class="counter-row">
      <div class="counter"><span style="color:var(--red)">●</span> Contradicted: <span class="num" id="cntContradicted">0</span></div>
      <div class="counter"><span style="color:var(--green)">●</span> Supported: <span class="num" id="cntSupported">0</span></div>
      <div class="counter"><span style="color:var(--orange)">●</span> Unverifiable: <span class="num" id="cntUnverifiable">0</span></div>
    </div>
    <div class="claims-scroll" id="claimsScroll">
      <div class="empty-state" id="claimsEmpty">
        <span class="icon">🔍</span>
        <span>Claims will appear here</span>
        <span style="font-size:11px">Factual claims are detected and verified against vault documents</span>
      </div>
    </div>
  </div>
</div>

<script>
// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
var WORKLET_CODE = __WORKLET_CODE__;
var WS_URL_BASE  = "__WS_URL__";
var MAX_SEGMENTS = 200;
var MAX_CLAIMS   = 50;
var MAX_RECONNECT = 3;
var SPEAKER_COLOURS = [
  '#4da6ff','#2ecc71','#f39c12','#e74c3c',
  '#9b59b6','#1abc9c','#e67e22','#3498db'
];

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
var ws = null;
var audioCtx = null;
var mediaStream = null;
var workletNode = null;
var isRecording = false;
var segmentCount = 0;
var speakersSet = new Set();
var interimEl = null;
var claimsMap = {};
var claimCardCount = 0;
var counts = { contradicted: 0, supported: 0, unverifiable: 0 };
var reconnectAttempts = 0;
var reconnectDelay = 1000;
var timerInterval = null;
var recordingStartTime = 0;
var smoothedRms = 0;
var currentSampleRate = 16000;

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
var statusDot      = document.getElementById('statusDot');
var statusText     = document.getElementById('statusText');
var btnStart       = document.getElementById('btnStart');
var btnStop        = document.getElementById('btnStop');
var volumeMeter    = document.getElementById('volumeMeter');
var volumeFill     = document.getElementById('volumeFill');
var vadBadge       = document.getElementById('vadBadge');
var timerEl        = document.getElementById('timer');
var transcriptScroll = document.getElementById('transcriptScroll');
var transcriptEmpty  = document.getElementById('transcriptEmpty');
var claimsScroll   = document.getElementById('claimsScroll');
var claimsEmpty    = document.getElementById('claimsEmpty');
var alertBanner    = document.getElementById('alertBanner');
var alertTextEl    = document.getElementById('alertText');
var speakerCountEl = document.getElementById('speakerCount');
var claimCountEl   = document.getElementById('claimCount');
var cntContradicted = document.getElementById('cntContradicted');
var cntSupported   = document.getElementById('cntSupported');
var cntUnverifiable = document.getElementById('cntUnverifiable');

// ---------------------------------------------------------------------------
// Audio capture — AudioWorklet + VAD
// ---------------------------------------------------------------------------

async function startRecording() {
  try {
    setStatus('connecting', 'Requesting microphone…');
    btnStart.disabled = true;
    reconnectAttempts = 0;
    reconnectDelay = 1000;

    // 1. Acquire microphone
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
    });

    // Detect mic disconnect (e.g. USB mic unplugged)
    mediaStream.getTracks()[0].onended = function() {
      setStatus('error', 'Microphone disconnected');
      stopRecording();
    };

    // 2. AudioContext — request 16 kHz, browser may give native rate
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    if (audioCtx.state === 'suspended') await audioCtx.resume();
    currentSampleRate = audioCtx.sampleRate;

    // 3. Register AudioWorklet processor from inline code
    var blob = new Blob([WORKLET_CODE], { type: 'application/javascript' });
    var workletUrl = URL.createObjectURL(blob);
    try {
      await audioCtx.audioWorklet.addModule(workletUrl);
    } catch(modErr) {
      URL.revokeObjectURL(workletUrl);
      setStatus('error', 'AudioWorklet not supported — use Chrome or Firefox');
      cleanupAudio();
      btnStart.disabled = false;
      return;
    }
    URL.revokeObjectURL(workletUrl);

    // 4. Wire audio graph: mic → worklet → destination (keeps node alive)
    var source = audioCtx.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioCtx, 'vad-processor');
    source.connect(workletNode);
    workletNode.connect(audioCtx.destination);

    // 5. Handle worklet messages (audio chunks + VAD state)
    workletNode.port.onmessage = function(e) {
      var data = e.data;
      updateVolumeMeter(data.rms, data.speaking);
      if (data.audio && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(data.audio);
      }
    };

    // 6. Connect WebSocket
    connectWebSocket();

  } catch(err) {
    console.error('Start recording failed:', err);
    if (err.name === 'NotAllowedError') {
      setStatus('error', 'Microphone permission denied');
    } else if (err.name === 'NotFoundError') {
      setStatus('error', 'No microphone found');
    } else {
      setStatus('error', err.message || 'Failed to start');
    }
    cleanupAudio();
    btnStart.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// WebSocket connection (with reconnect support)
// ---------------------------------------------------------------------------

function connectWebSocket() {
  var wsUrl = WS_URL_BASE + '&sample_rate=' + currentSampleRate;
  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  ws.onopen = function() {
    setStatus('recording', 'Listening…');
    btnStop.disabled = false;
    isRecording = true;
    reconnectAttempts = 0;
    reconnectDelay = 1000;

    // Show audio indicators
    volumeMeter.style.display = 'block';
    vadBadge.style.display = 'inline-block';

    if (!timerInterval) startTimer();
  };

  ws.onmessage = function(event) {
    try {
      handleMessage(JSON.parse(event.data));
    } catch(e) {
      console.error('Message parse error:', e);
    }
  };

  ws.onclose = function(event) {
    if (isRecording) {
      // Unexpected close — attempt reconnect
      if (event.code !== 1000 && event.code !== 1001 && reconnectAttempts < MAX_RECONNECT) {
        attemptReconnect();
      } else {
        var reason = event.code === 4001 ? 'Authentication failed'
          : event.code === 4003 ? 'Not a vault member'
          : 'Connection closed';
        setStatus('error', reason);
        stopRecording();
      }
    }
  };

  ws.onerror = function() {
    console.error('WebSocket error');
  };
}

function attemptReconnect() {
  reconnectAttempts++;
  setStatus('connecting', 'Reconnecting (' + reconnectAttempts + '/' + MAX_RECONNECT + ')…');
  setTimeout(function() {
    if (isRecording) connectWebSocket();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 8000);
}

// ---------------------------------------------------------------------------
// Stop & cleanup
// ---------------------------------------------------------------------------

function stopRecording() {
  isRecording = false;

  // Signal backend to finalize
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: 'stop' })); } catch(e) {}
    setTimeout(function() { if (ws) { ws.close(); ws = null; } }, 300);
  } else {
    ws = null;
  }

  cleanupAudio();
  stopTimer();

  volumeMeter.style.display = 'none';
  vadBadge.style.display = 'none';
  volumeFill.style.width = '0%';

  btnStart.disabled = false;
  btnStop.disabled = true;
  if (statusDot.className.indexOf('error') === -1) {
    setStatus('connected', 'Stopped');
  }
}

function cleanupAudio() {
  if (workletNode) {
    try { workletNode.port.close(); } catch(e) {}
    try { workletNode.disconnect(); } catch(e) {}
    workletNode = null;
  }
  if (audioCtx && audioCtx.state !== 'closed') {
    audioCtx.close().catch(function() {});
    audioCtx = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(function(t) { t.stop(); });
    mediaStream = null;
  }
}

function clearAll() {
  while (transcriptScroll.lastChild && transcriptScroll.lastChild !== transcriptEmpty) {
    transcriptScroll.removeChild(transcriptScroll.lastChild);
  }
  while (transcriptScroll.firstChild && transcriptScroll.firstChild !== transcriptEmpty) {
    transcriptScroll.removeChild(transcriptScroll.firstChild);
  }
  transcriptEmpty.style.display = 'flex';

  while (claimsScroll.lastChild && claimsScroll.lastChild !== claimsEmpty) {
    claimsScroll.removeChild(claimsScroll.lastChild);
  }
  while (claimsScroll.firstChild && claimsScroll.firstChild !== claimsEmpty) {
    claimsScroll.removeChild(claimsScroll.firstChild);
  }
  claimsEmpty.style.display = 'flex';

  alertBanner.classList.remove('visible');
  segmentCount = 0;
  claimCardCount = 0;
  interimEl = null;
  speakersSet.clear();
  claimsMap = {};
  counts = { contradicted: 0, supported: 0, unverifiable: 0 };
  speakerCountEl.textContent = '0 speakers';
  timerEl.textContent = '00:00';
  timerEl.className = 'timer';
  updateCounters();
}

// ---------------------------------------------------------------------------
// Volume meter & VAD indicator
// ---------------------------------------------------------------------------

function updateVolumeMeter(rms, speaking) {
  smoothedRms = smoothedRms * 0.7 + rms * 0.3;
  var pct = Math.min(100, Math.round(smoothedRms * 800));
  volumeFill.style.width = pct + '%';
  volumeFill.className = 'volume-fill ' + (speaking ? 'speaking' : 'silent');
  vadBadge.textContent = speaking ? 'VOICE' : 'SILENT';
  vadBadge.className = 'vad-badge ' + (speaking ? 'speaking' : 'silent');
}

// ---------------------------------------------------------------------------
// Timer
// ---------------------------------------------------------------------------

function startTimer() {
  recordingStartTime = Date.now();
  timerEl.className = 'timer active';
  timerInterval = setInterval(function() {
    var elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
    var m = Math.floor(elapsed / 60).toString().padStart(2, '0');
    var s = (elapsed % 60).toString().padStart(2, '0');
    timerEl.textContent = m + ':' + s;
  }, 1000);
}

function stopTimer() {
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  timerEl.className = 'timer';
}

// ---------------------------------------------------------------------------
// Tab visibility — resume suspended AudioContext
// ---------------------------------------------------------------------------

document.addEventListener('visibilitychange', function() {
  if (document.visibilityState === 'visible' && audioCtx && audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
});

// ---------------------------------------------------------------------------
// Message handlers
// ---------------------------------------------------------------------------

function handleMessage(msg) {
  switch(msg.type) {
    case 'transcript':      handleTranscript(msg); break;
    case 'claim_detected':  handleClaimDetected(msg); break;
    case 'claim_verified':  handleClaimVerified(msg); break;
    case 'error':           setStatus('error', msg.message || 'Server error'); break;
  }
}

function handleTranscript(msg) {
  transcriptEmpty.style.display = 'none';
  speakersSet.add(msg.speaker);
  speakerCountEl.textContent = speakersSet.size + ' speaker' + (speakersSet.size !== 1 ? 's' : '');

  var colour = SPEAKER_COLOURS[msg.speaker % SPEAKER_COLOURS.length];

  if (!msg.is_final) {
    // Interim — update or create faded preview element
    if (!interimEl) {
      interimEl = document.createElement('div');
      interimEl.className = 'segment interim';
      transcriptScroll.appendChild(interimEl);
    }
    interimEl.style.borderLeftColor = colour;
    interimEl.innerHTML =
      '<div class="segment-header">' +
        '<span class="speaker-badge" style="background:' + colour + '">Speaker ' + msg.speaker + '</span>' +
        '<span>' + formatTime(msg.start) + '</span>' +
      '</div>' +
      '<div class="segment-text">' + escapeHtml(msg.text) + '</div>';
    scrollToBottom(transcriptScroll);
    return;
  }

  // Final — replace interim, add permanent segment
  if (interimEl) { interimEl.remove(); interimEl = null; }

  segmentCount++;
  enforceSegmentCap();

  var el = document.createElement('div');
  el.className = 'segment';
  el.style.borderLeftColor = colour;
  el.innerHTML =
    '<div class="segment-header">' +
      '<span class="speaker-badge" style="background:' + colour + '">Speaker ' + msg.speaker + '</span>' +
      '<span>' + formatTime(msg.start) + ' → ' + formatTime(msg.end) + '</span>' +
      '<span style="margin-left:auto">' + (msg.confidence * 100).toFixed(0) + '%</span>' +
    '</div>' +
    '<div class="segment-text">' + escapeHtml(msg.text) + '</div>';
  transcriptScroll.appendChild(el);
  scrollToBottom(transcriptScroll);
}

function enforceSegmentCap() {
  var segs = transcriptScroll.querySelectorAll('.segment:not(.interim)');
  while (segs.length >= MAX_SEGMENTS) {
    segs[0].remove();
    segs = transcriptScroll.querySelectorAll('.segment:not(.interim)');
  }
}

function handleClaimDetected(msg) {
  claimsEmpty.style.display = 'none';
  enforceClaimCap();

  var card = document.createElement('div');
  card.className = 'claim-card verifying';
  card.id = 'claim-' + msg.claim_id;
  card.innerHTML =
    '<span class="verdict-badge verifying">⏳ Verifying…</span>' +
    '<div class="claim-speaker">Speaker ' + msg.speaker + '</div>' +
    '<div class="claim-text">' + escapeHtml(msg.text) + '</div>';

  if (claimsScroll.firstChild && claimsScroll.firstChild !== claimsEmpty) {
    claimsScroll.insertBefore(card, claimsScroll.firstChild);
  } else {
    claimsScroll.appendChild(card);
  }

  claimCardCount++;
  claimsMap[msg.claim_id] = { text: msg.text, speaker: msg.speaker };
  updateClaimCount();
}

function enforceClaimCap() {
  var cards = claimsScroll.querySelectorAll('.claim-card');
  while (cards.length >= MAX_CLAIMS) {
    cards[cards.length - 1].remove();
    cards = claimsScroll.querySelectorAll('.claim-card');
  }
}

function handleClaimVerified(msg) {
  var card = document.getElementById('claim-' + msg.claim_id);
  if (!card) return;

  var verdict = msg.verdict;
  card.className = 'claim-card ' + verdict;

  counts[verdict] = (counts[verdict] || 0) + 1;
  updateCounters();

  // Evidence HTML
  var evidenceHtml = '';
  if (msg.evidence && msg.evidence.length > 0) {
    for (var i = 0; i < msg.evidence.length; i++) {
      var ev = msg.evidence[i];
      evidenceHtml +=
        '<div class="claim-evidence">' +
          '📄 <strong>' + escapeHtml(ev.doc_title || 'Document') + '</strong>' +
          (ev.page ? ' (p.' + ev.page + ')' : '') +
          (ev.section ? ' · ' + escapeHtml(ev.section) : '') +
          '<br/>"' + escapeHtml(ev.quote || '') + '"' +
        '</div>';
    }
  }

  var confColour = verdict === 'supported' ? 'var(--green)'
    : verdict === 'contradicted' ? 'var(--red)' : 'var(--orange)';
  var confPct = ((msg.confidence || 0) * 100).toFixed(0);
  var verdictIcon = verdict === 'supported' ? '✅' : verdict === 'contradicted' ? '❌' : '⚠️';
  var claimInfo = claimsMap[msg.claim_id] || {};

  card.innerHTML =
    '<span class="verdict-badge ' + verdict + '">' + verdictIcon + ' ' + verdict.toUpperCase() + '</span>' +
    '<div class="claim-speaker">Speaker ' + (claimInfo.speaker !== undefined ? claimInfo.speaker : '?') + '</div>' +
    '<div class="claim-text">' + escapeHtml(msg.claim_text) + '</div>' +
    '<div class="confidence-bar"><div class="confidence-fill" style="width:' + confPct + '%;background:' + confColour + '"></div></div>' +
    '<div class="claim-explanation">' + escapeHtml(msg.explanation || '') + '</div>' +
    evidenceHtml;

  if (verdict === 'contradicted') {
    showAlert(msg.claim_text);
    playAlertSound();
  }
}

// ---------------------------------------------------------------------------
// Alert
// ---------------------------------------------------------------------------

function showAlert(text) {
  alertTextEl.textContent = text.length > 80 ? text.slice(0, 80) + '…' : text;
  alertBanner.classList.remove('visible');
  void alertBanner.offsetWidth;
  alertBanner.classList.add('visible');
  setTimeout(function() { alertBanner.classList.remove('visible'); }, 8000);
}

function playAlertSound() {
  try {
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.type = 'sine';
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.4);
  } catch(e) {}
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function setStatus(state, text) {
  statusDot.className = 'status-dot ' + state;
  statusText.textContent = text;
}

function formatTime(s) {
  if (!s && s !== 0) return '0:00';
  var m = Math.floor(s / 60);
  var sec = Math.floor(s % 60);
  return m + ':' + sec.toString().padStart(2, '0');
}

function escapeHtml(text) {
  var d = document.createElement('div');
  d.textContent = text || '';
  return d.innerHTML;
}

function scrollToBottom(el) { el.scrollTop = el.scrollHeight; }

function updateClaimCount() {
  var total = Object.keys(claimsMap).length;
  claimCountEl.textContent = total + ' claim' + (total !== 1 ? 's' : '');
}

function updateCounters() {
  cntContradicted.textContent = counts.contradicted;
  cntSupported.textContent = counts.supported;
  cntUnverifiable.textContent = counts.unverifiable;
  updateClaimCount();
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Component builder
# ---------------------------------------------------------------------------

def _build_live_component(ws_url: str) -> str:
    """Build the live transcription HTML component.

    Performs two simple placeholder replacements on the template:
      - ``__WORKLET_CODE__`` → JSON-encoded AudioWorklet processor source
      - ``__WS_URL__``       → WebSocket URL with session_id

    No f-string brace escaping needed — the template is a plain string.
    """
    html = _LIVE_COMPONENT_HTML
    html = html.replace("__WORKLET_CODE__", json.dumps(_WORKLET_PROCESSOR_JS))
    html = html.replace("__WS_URL__", ws_url)
    return html


# ---------------------------------------------------------------------------
# UI Pages
# ---------------------------------------------------------------------------

def _render_login() -> None:
    st.title("🔐 AIlways — Login")
    st.caption("Log in to access real-time claim verification")

    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

        if submitted and email and password:
            _login(email, password)
            if st.session_state.authenticated:
                st.rerun()


def _render_main() -> None:
    """Render the main real-time transcription interface."""

    # ---- Sidebar ----
    with st.sidebar:
        st.header("⚙️ Configuration")
        st.caption(f"Logged in as **{st.session_state.user_name}**")
        st.divider()

        st.subheader("📦 Vault")
        if st.button("Refresh Vaults", use_container_width=True):
            st.session_state.vaults = _api_get("/vaults") or []

        if not st.session_state.vaults:
            st.session_state.vaults = _api_get("/vaults") or []

        vaults = st.session_state.vaults
        if not vaults:
            st.warning("No vaults found. Create one in the main app first.")
            return

        vault_options = {v["name"]: v["id"] for v in vaults}
        selected_name = st.selectbox("Select vault for verification", list(vault_options.keys()))
        vault_id = vault_options[selected_name]

        st.divider()
        st.markdown(
            "**How it works:**\n"
            "1. Click **Start** to begin recording\n"
            "2. Speak — transcript appears in real-time\n"
            "3. Voice activity detection gates audio\n"
            "4. Factual claims are auto-detected\n"
            "5. Each claim is verified against vault docs\n"
            "6. **Contradictions trigger alerts** 🚨"
        )

        st.divider()
        if st.button("Logout", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

    # ---- Main area ----
    st.markdown(
        "<h2 style='margin-bottom:0'>🎙️ AIlways — Live Claim Verification</h2>"
        "<p style='color:#888;margin-top:4px'>"
        "Real-time transcription with speaker diarization · "
        "VAD-gated audio streaming · "
        "Automatic claim detection · "
        "RAG-powered verification against vault documents"
        "</p>",
        unsafe_allow_html=True,
    )

    # Build the WebSocket URL with auth
    session_id = st.session_state.session_id
    ws_url = f"{WS_BASE}/vaults/{vault_id}/transcribe/live?session_id={session_id}"

    # Render the live component
    html_content = _build_live_component(ws_url)
    components.html(html_content, height=680, scrolling=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="AIlways — Live Claim Verification",
        page_icon="🎙️",
        layout="wide",
    )
    _init_state()

    if not st.session_state.authenticated:
        _render_login()
    else:
        _render_main()


if __name__ == "__main__":
    main()

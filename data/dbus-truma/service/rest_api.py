import asyncio
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Optional


WEB_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Truma Control</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow+Condensed:wght@300;400;600;700&display=swap');

  :root {
    --bg:        #0f1014;
    --surface:   #181b22;
    --border:    #272c38;
    --amber:     #e8a030;
    --amber-dim: #7a5218;
    --blue:      #4a9eca;
    --green:     #3dba6f;
    --red:       #e05050;
    --muted:     #5a6070;
    --text:      #c8cdd8;
    --text-hi:   #eceef2;
    --mono:      'Share Tech Mono', monospace;
    --ui:        'Barlow Condensed', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--ui);
    font-size: 15px;
    min-height: 100vh;
    padding: 16px;
  }

  /* ── Header ── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 0 20px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
  }

  .logo {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-hi);
  }

  .logo span { color: var(--amber); }

  .status-pill {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 600;
  }

  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--muted);
    transition: background 0.4s, box-shadow 0.4s;
  }

  .status-dot.connected {
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
  }

  .status-dot.disconnected {
    background: var(--red);
    box-shadow: 0 0 6px var(--red);
  }

  /* ── Grid ── */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 14px;
  }

  /* ── Card ── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 18px 20px;
    position: relative;
    overflow: hidden;
  }

  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--border);
    transition: background 0.4s;
  }

  .card.active::before { background: var(--amber); }
  .card.active-blue::before { background: var(--blue); }
  .card.active-green::before { background: var(--green); }

  .card-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 14px;
  }

  /* ── Temp readout ── */
  .temp-row {
    display: flex;
    align-items: baseline;
    gap: 20px;
    margin-bottom: 14px;
  }

  .temp-block { display: flex; flex-direction: column; }

  .temp-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 2px;
  }

  .temp-value {
    font-family: var(--mono);
    font-size: 44px;
    line-height: 1;
    color: var(--amber);
    transition: color 0.4s;
  }

  .temp-value.dim { color: var(--amber-dim); }
  .temp-value.blue { color: var(--blue); }

  .temp-unit {
    font-family: var(--mono);
    font-size: 18px;
    color: var(--muted);
    align-self: flex-end;
    margin-bottom: 5px;
  }

  .temp-target {
    font-family: var(--mono);
    font-size: 22px;
    color: var(--muted);
    margin-bottom: 6px;
  }

  .target-row {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  /* ── Mode badge ── */
  .mode-badge {
    display: inline-block;
    padding: 3px 9px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    background: #22262f;
    color: var(--muted);
    margin-bottom: 14px;
    transition: background 0.3s, color 0.3s;
  }

  .mode-badge.on {
    background: rgba(232, 160, 48, 0.15);
    color: var(--amber);
  }

  .mode-badge.on-blue {
    background: rgba(74, 158, 202, 0.15);
    color: var(--blue);
  }

  /* ── Button group ── */
  .btn-group {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .btn {
    flex: 1;
    min-width: 60px;
    padding: 9px 12px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: #22262f;
    color: var(--muted);
    font-family: var(--ui);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    transition: border-color 0.2s, color 0.2s, background 0.2s, box-shadow 0.2s;
  }

  .btn:hover {
    border-color: var(--amber);
    color: var(--amber);
    background: rgba(232, 160, 48, 0.08);
  }

  .btn.active {
    border-color: var(--amber);
    color: var(--amber);
    background: rgba(232, 160, 48, 0.12);
    box-shadow: 0 0 0 1px var(--amber-dim);
  }

  .btn.active-blue {
    border-color: var(--blue);
    color: var(--blue);
    background: rgba(74, 158, 202, 0.12);
    box-shadow: 0 0 0 1px rgba(74, 158, 202, 0.4);
  }

  .btn:active { transform: scale(0.97); }

  /* ── Energy toggles ── */
  .energy-row {
    display: flex;
    gap: 10px;
    margin-bottom: 0;
  }

  .toggle-card {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 12px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: #22262f;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }

  .toggle-card:hover { border-color: var(--amber); }

  .toggle-card.on {
    border-color: var(--amber);
    background: rgba(232, 160, 48, 0.1);
  }

  .toggle-icon {
    font-size: 22px;
    line-height: 1;
  }

  .toggle-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .toggle-state {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--muted);
    transition: color 0.3s;
  }

  .toggle-card.on .toggle-state { color: var(--amber); }

  /* ── System info ── */
  .sys-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }

  .sys-item { display: flex; flex-direction: column; gap: 3px; }

  .sys-item-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .sys-item-value {
    font-family: var(--mono);
    font-size: 20px;
    color: var(--text-hi);
  }

  .sys-item-value.flame-on { color: var(--amber); }
  .sys-item-value.flame-off { color: var(--muted); }

  /* ── Error banner ── */
  .error-banner {
    display: none;
    padding: 10px 14px;
    background: rgba(224, 80, 80, 0.12);
    border: 1px solid var(--red);
    border-radius: 4px;
    color: var(--red);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    margin-bottom: 14px;
  }

  .error-banner.visible { display: block; }

  /* ── Footer ── */
  footer {
    margin-top: 20px;
    padding-top: 14px;
    border-top: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: var(--muted);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  #last-update { font-family: var(--mono); font-size: 11px; }

  /* ── Responsive ── */
  @media (max-width: 500px) {
    .temp-value { font-size: 36px; }
    .grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">Truma <span>Control</span></div>
  <div class="status-pill">
    <div class="status-dot" id="conn-dot"></div>
    <span id="conn-label">Connecting</span>
  </div>
</header>

<div class="error-banner" id="error-banner"></div>

<div class="grid">

  <!-- Room Climate -->
  <div class="card" id="card-room">
    <div class="card-label">Room Climate</div>
    <div class="temp-row">
      <div class="temp-block">
        <div class="temp-label">Current</div>
        <div class="temp-value" id="room-current">--</div>
      </div>
      <div class="temp-unit">°C</div>
      <div class="temp-block">
        <div class="temp-label">Target</div>
        <div class="temp-target" id="room-target">--°</div>
      </div>
    </div>
    <div class="mode-badge" id="room-mode-badge">OFF</div>
    <div class="btn-group">
      <button class="btn" id="btn-room-off"
        onclick="sendCommand('RoomClimate','Mode',0)">Off</button>
      <button class="btn" id="btn-room-heat"
        onclick="sendCommand('RoomClimate','Mode',1)">Heat</button>
      <button class="btn" id="btn-room-vent"
        onclick="sendCommand('RoomClimate','Mode',2)">Vent</button>
      <button class="btn" id="btn-room-auto"
        onclick="sendCommand('RoomClimate','Mode',3)">Auto</button>
    </div>
  </div>

  <!-- Water Heating -->
  <div class="card" id="card-water">
    <div class="card-label">Water Heating</div>
    <div class="temp-row">
      <div class="temp-block">
        <div class="temp-label">Current</div>
        <div class="temp-value blue" id="water-current">--</div>
      </div>
      <div class="temp-unit">°C</div>
    </div>
    <div class="mode-badge" id="water-mode-badge">OFF</div>
    <div class="btn-group">
      <button class="btn" id="btn-water-off"
        onclick="sendCommand('WaterHeating','Active',0)">Off</button>
      <button class="btn" id="btn-water-40"
        onclick="waterOn(0)">40°</button>
      <button class="btn" id="btn-water-60"
        onclick="waterOn(1)">60°</button>
      <button class="btn" id="btn-water-70"
        onclick="waterOn(2)">70°</button>
    </div>
  </div>

  <!-- Energy Source -->
  <div class="card" id="card-energy">
    <div class="card-label">Energy Source</div>
    <div class="energy-row">
      <div class="toggle-card" id="toggle-diesel"
           onclick="toggleDiesel()">
        <div class="toggle-icon">&#128293;</div>
        <div class="toggle-label">Diesel</div>
        <div class="toggle-state" id="diesel-state">--</div>
      </div>
      <div class="toggle-card" id="toggle-electric"
           onclick="toggleElectric()">
        <div class="toggle-icon">&#9889;</div>
        <div class="toggle-label">Electric</div>
        <div class="toggle-state" id="electric-state">--</div>
      </div>
    </div>
  </div>

  <!-- System -->
  <div class="card" id="card-system">
    <div class="card-label">System</div>
    <div class="sys-grid">
      <div class="sys-item">
        <div class="sys-item-label">Flame</div>
        <div class="sys-item-value" id="sys-flame">--</div>
      </div>
      <div class="sys-item">
        <div class="sys-item-label">Voltage</div>
        <div class="sys-item-value" id="sys-voltage">--</div>
      </div>
      <div class="sys-item">
        <div class="sys-item-label">Int. Temp</div>
        <div class="sys-item-value" id="sys-int-temp">--</div>
      </div>
      <div class="sys-item">
        <div class="sys-item-label">Errors</div>
        <div class="sys-item-value" id="sys-errors">--</div>
      </div>
    </div>
  </div>

</div>

<footer>
  <span>Truma LIN Bridge</span>
  <span id="last-update">--</span>
</footer>

<script>
  // ── State cache (for toggle logic) ──
  var _state = null;

  // ── Helpers ──
  function fmt(v, decimals) {
    if (v === null || v === undefined) return '--';
    return parseFloat(v).toFixed(decimals !== undefined ? decimals : 1);
  }

  function setActive(card, isActive, colorClass) {
    card.classList.remove('active', 'active-blue', 'active-green');
    if (isActive) card.classList.add(colorClass || 'active');
  }

  function setBadge(el, text, isOn, colorClass) {
    el.textContent = text;
    el.classList.remove('on', 'on-blue');
    if (isOn) el.classList.add(colorClass || 'on');
  }

  function clearBtns(prefix) {
    ['off','heat','vent','auto','40','60','70'].forEach(function(s) {
      var b = document.getElementById('btn-' + prefix + '-' + s);
      if (b) { b.classList.remove('active', 'active-blue'); }
    });
  }

  // ── Room Climate ──
  var ROOM_MODE_NAMES = { 0: 'OFF', 1: 'HEAT', 2: 'VENT', 3: 'AUTO' };
  var ROOM_BTN_IDS    = { 0: 'off', 1: 'heat', 2: 'vent', 3: 'auto' };

  function updateRoom(rc) {
    document.getElementById('room-current').textContent = fmt(rc.current_temp_c);
    document.getElementById('room-target').textContent  =
      rc.target_temp_c !== null && rc.target_temp_c !== undefined
        ? fmt(rc.target_temp_c, 0) + '°' : '--°';

    var mode = rc.mode !== undefined ? rc.mode : 0;
    var name = ROOM_MODE_NAMES[mode] || rc.mode_name || 'OFF';
    var isOn = mode !== 0;
    setBadge(document.getElementById('room-mode-badge'), name, isOn);
    setActive(document.getElementById('card-room'), isOn);

    clearBtns('room');
    var btnId = 'btn-room-' + (ROOM_BTN_IDS[mode] || 'off');
    var btn = document.getElementById(btnId);
    if (btn) btn.classList.add('active');
  }

  // ── Water Heating ──
  // mode_name mapping from protocol: OFF=0, TEMP_40=1, TEMP_60=2, TEMP_70=3
  var WATER_MODE_LABELS = { 0: 'OFF', 1: '40 °C', 2: '60 °C', 3: '70 °C' };
  var WATER_BTN_IDS     = { 0: 'off', 1: '40',    2: '60',    3: '70'    };

  function updateWater(wh) {
    document.getElementById('water-current').textContent = fmt(wh.current_temp_c);
    var mode = wh.mode !== undefined ? wh.mode : 0;
    var label = WATER_MODE_LABELS[mode] || wh.mode_name || 'OFF';
    var isOn = mode !== 0;
    setBadge(document.getElementById('water-mode-badge'), label, isOn, 'on-blue');
    setActive(document.getElementById('card-water'), isOn, 'active-blue');

    clearBtns('water');
    var btnId = 'btn-water-' + (WATER_BTN_IDS[mode] || 'off');
    var btn = document.getElementById(btnId);
    if (btn) btn.classList.add('active-blue');
  }

  // ── Energy ──
  function updateEnergy(en) {
    var dieselOn   = en.diesel   === 1;
    var electricOn = en.electric === 1;

    var td = document.getElementById('toggle-diesel');
    var te = document.getElementById('toggle-electric');
    td.classList.toggle('on', dieselOn);
    te.classList.toggle('on', electricOn);
    document.getElementById('diesel-state').textContent   = dieselOn   ? 'ON' : 'OFF';
    document.getElementById('electric-state').textContent = electricOn ? 'ON' : 'OFF';
  }

  // ── System ──
  function updateSystem(sys) {
    var flameEl = document.getElementById('sys-flame');
    var on = sys.flame_status === 1;
    flameEl.textContent = on ? 'ON' : 'OFF';
    flameEl.className = 'sys-item-value ' + (on ? 'flame-on' : 'flame-off');

    document.getElementById('sys-voltage').textContent =
      sys.voltage_v !== null && sys.voltage_v !== undefined
        ? fmt(sys.voltage_v, 1) + ' V' : '--';

    document.getElementById('sys-int-temp').textContent =
      fmt(sys.internal_temp_c) + ' °C';

    document.getElementById('sys-errors').textContent =
      sys.error_codes ? String(sys.error_codes) : 'None';
  }

  // ── Connection ──
  function updateConn(connected) {
    var dot   = document.getElementById('conn-dot');
    var label = document.getElementById('conn-label');
    dot.className = 'status-dot ' + (connected ? 'connected' : 'disconnected');
    label.textContent = connected ? 'Connected' : 'Disconnected';
  }

  // ── Time ──
  function updateTime(ts) {
    if (!ts) return;
    var d = new Date(ts * 1000);
    var hh = String(d.getHours()).padStart(2,'0');
    var mm = String(d.getMinutes()).padStart(2,'0');
    var ss = String(d.getSeconds()).padStart(2,'0');
    document.getElementById('last-update').textContent =
      'Updated ' + hh + ':' + mm + ':' + ss;
  }

  // ── Main status poll ──
  function poll() {
    fetch('/api/status')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        _state = data;
        document.getElementById('error-banner').classList.remove('visible');

        updateConn(data.connected);
        if (data.room_climate)  updateRoom(data.room_climate);
        if (data.water_heating) updateWater(data.water_heating);
        if (data.energy)        updateEnergy(data.energy);
        if (data.system)        updateSystem(data.system);
        updateTime(data.last_update);
      })
      .catch(function(err) {
        updateConn(false);
        var eb = document.getElementById('error-banner');
        eb.textContent = 'Cannot reach API: ' + err.message;
        eb.classList.add('visible');
      });
  }

  // ── Commands ──
  function sendCommand(topic, param, value) {
    fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic, param: param, value: value })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        var eb = document.getElementById('error-banner');
        eb.textContent = 'Command failed: ' + data.error;
        eb.classList.add('visible');
      }
      setTimeout(poll, 400);
    })
    .catch(function(err) {
      var eb = document.getElementById('error-banner');
      eb.textContent = 'Command error: ' + err.message;
      eb.classList.add('visible');
    });
  }

  function waterOn(mode) {
    // Activate first, then set mode — Truma rejects mode changes when inactive
    sendCommand('WaterHeating', 'Active', 1);
    setTimeout(function() { sendCommand('WaterHeating', 'Mode', mode); }, 500);
  }

  function toggleDiesel() {
    if (!_state || !_state.energy) return;
    var current = _state.energy.diesel === 1 ? 1 : 0;
    sendCommand('Energy', 'Diesel', current === 1 ? 0 : 1);
  }

  function toggleElectric() {
    if (!_state || !_state.energy) return;
    var current = _state.energy.electric === 1 ? 1 : 0;
    sendCommand('Energy', 'Electric', current === 1 ? 0 : 1);
  }

  // ── Boot ──
  poll();
  setInterval(poll, 3000);
</script>
</body>
</html>"""


SETUP_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Truma Setup</title>
<style>
  :root {
    --bg: #0f1014; --surface: #181b22; --border: #272c38;
    --amber: #e8a030; --blue: #4a9eca; --green: #3dba6f; --red: #e05050;
    --muted: #5a6070; --text: #c8cdd8; --text-hi: #eceef2;
    --mono: monospace; --ui: system-ui, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--ui);
         font-size: 15px; min-height: 100vh; padding: 16px; max-width: 600px; margin: 0 auto; }
  header { display: flex; align-items: center; justify-content: space-between;
           padding: 12px 0 20px; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
  .logo { font-size: 22px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-hi); }
  .logo span { color: var(--amber); }
  a.back { color: var(--muted); text-decoration: none; font-size: 13px; }
  a.back:hover { color: var(--amber); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
          padding: 18px 20px; margin-bottom: 14px; }
  .card-label { font-size: 11px; font-weight: 700; letter-spacing: 0.14em;
                text-transform: uppercase; color: var(--muted); margin-bottom: 14px; }
  .row { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
  .row:last-child { margin-bottom: 0; }
  label { font-size: 13px; color: var(--muted); min-width: 80px; font-weight: 600; }
  input[type=text], input[type=number] {
    flex: 1; background: #22262f; border: 1px solid var(--border); border-radius: 4px;
    padding: 8px 10px; color: var(--text-hi); font-family: var(--mono); font-size: 14px; }
  input:focus { outline: none; border-color: var(--amber); }
  .btn { padding: 9px 16px; border: 1px solid var(--border); border-radius: 4px;
         background: #22262f; color: var(--muted); font-family: var(--ui); font-size: 13px;
         font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; cursor: pointer; }
  .btn:hover { border-color: var(--amber); color: var(--amber); background: rgba(232,160,48,0.08); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn.primary { border-color: var(--amber); color: var(--amber); }
  .btn.danger { border-color: var(--red); color: var(--red); }
  .btn.danger:hover { background: rgba(224,80,80,0.1); }
  .status { font-family: var(--mono); font-size: 13px; padding: 8px 0; }
  .status.ok { color: var(--green); }
  .status.err { color: var(--red); }
  .status.info { color: var(--blue); }
  .device-list { list-style: none; }
  .device-list li { display: flex; justify-content: space-between; align-items: center;
    padding: 10px 12px; border: 1px solid var(--border); border-radius: 4px; margin-bottom: 6px;
    background: #22262f; }
  .device-name { color: var(--text-hi); font-weight: 600; }
  .device-addr { color: var(--muted); font-family: var(--mono); font-size: 12px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 10px;
           font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; margin-left: 8px; }
  .badge.paired { background: rgba(61,186,111,0.15); color: var(--green); }
  .badge.connected { background: rgba(74,158,202,0.15); color: var(--blue); }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border);
             border-top-color: var(--amber); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #log { background: #0a0b0e; border: 1px solid var(--border); border-radius: 4px; padding: 10px;
         font-family: var(--mono); font-size: 12px; max-height: 200px; overflow-y: auto;
         white-space: pre-wrap; color: var(--muted); margin-top: 10px; display: none; }
</style>
</head>
<body>

<header>
  <div class="logo">Truma <span>Setup</span></div>
  <a class="back" href="/">&#8592; Control Panel</a>
</header>

<!-- Connection Status -->
<div class="card">
  <div class="card-label">Connection Status</div>
  <div class="row">
    <label>BLE</label>
    <span class="status" id="ble-status">Checking...</span>
  </div>
  <div class="row">
    <label>MQTT</label>
    <span class="status" id="mqtt-status">Checking...</span>
  </div>
</div>

<!-- Service Stats -->
<div class="card">
  <div class="card-label">Service</div>
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px">
    <div><label>Uptime</label><div class="status" id="stat-uptime">--</div></div>
    <div><label>Params</label><div class="status" id="stat-params">--</div></div>
    <div><label>Address</label><div class="status" id="stat-addr">--</div></div>
    <div><label>Last Update</label><div class="status" id="stat-update">--</div></div>
  </div>
  <div class="row">
    <button class="btn danger" onclick="restartService()">Restart Service</button>
    <span class="status" id="restart-status"></span>
  </div>
</div>

<!-- BLE Pairing -->
<div class="card">
  <div class="card-label">BLE Pairing</div>
  <div class="row">
    <button class="btn" id="btn-scan" onclick="bleScan()">Scan for Truma</button>
    <span id="scan-spinner" style="display:none"><span class="spinner"></span> Scanning...</span>
  </div>
  <ul class="device-list" id="device-list"></ul>
  <div class="row" id="pair-row" style="display:none">
    <label>Passkey</label>
    <input type="number" id="passkey" placeholder="6-digit code from panel" maxlength="6">
    <button class="btn primary" onclick="blePair()">Pair</button>
  </div>
  <div class="status" id="pair-status"></div>
</div>

<!-- MQTT Configuration -->
<div class="card">
  <div class="card-label">MQTT Broker</div>
  <div class="row">
    <label>Host</label>
    <input type="text" id="mqtt-host" placeholder="192.168.1.x">
  </div>
  <div class="row">
    <label>Port</label>
    <input type="number" id="mqtt-port" value="1883">
  </div>
  <div class="row">
    <button class="btn primary" onclick="saveMqtt()">Save</button>
    <span class="status" id="mqtt-save-status"></span>
  </div>
</div>

<!-- Pairing Management -->
<div class="card">
  <div class="card-label">Pairing</div>
  <div class="status" id="identity-info"></div>
  <div class="row" style="margin-top: 10px">
    <button class="btn danger" onclick="unpairDevice()">Unpair</button>
    <span class="status" id="identity-reset-status"></span>
  </div>
</div>

<!-- Adapters -->
<div class="card">
  <div class="card-label">BLE Adapters</div>
  <div id="adapter-list" class="status">Loading...</div>
</div>

<div id="log"></div>

<script>
var _selectedAddr = null;

function api(method, path, body) {
  var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  return fetch(path, opts).then(function(r) { return r.json(); });
}

function logMsg(msg) {
  var el = document.getElementById('log');
  el.style.display = 'block';
  el.textContent += new Date().toLocaleTimeString() + ' ' + msg + '\\n';
  el.scrollTop = el.scrollHeight;
}

function fmtUptime(s) {
  if (!s) return '--';
  var d = Math.floor(s/86400), h = Math.floor((s%86400)/3600), m = Math.floor((s%3600)/60);
  if (d > 0) return d + 'd ' + h + 'h ' + m + 'm';
  if (h > 0) return h + 'h ' + m + 'm';
  return m + 'm ' + Math.floor(s%60) + 's';
}

function fmtTime(ts) {
  if (!ts) return '--';
  var d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

// -- Status --
function loadStatus() {
  api('GET', '/api/health').then(function(d) {
    var el = document.getElementById('ble-status');
    if (d.connected) { el.textContent = 'Connected'; el.className = 'status ok'; }
    else { el.textContent = 'Disconnected'; el.className = 'status err'; }
    document.getElementById('stat-uptime').textContent = fmtUptime(d.uptime);
    document.getElementById('stat-params').textContent = d.raw_param_count || 0;
    document.getElementById('stat-addr').textContent = d.assigned_addr || '--';
    document.getElementById('stat-update').textContent = fmtTime(d.last_update);
  }).catch(function() {
    document.getElementById('ble-status').textContent = 'API unreachable';
    document.getElementById('ble-status').className = 'status err';
  });

  api('GET', '/api/setup/config').then(function(d) {
    document.getElementById('mqtt-host').value = d.mqtt_host || '';
    document.getElementById('mqtt-port').value = d.mqtt_port || 1883;
    var mel = document.getElementById('mqtt-status');
    if (d.mqtt_enabled && d.mqtt_host) { mel.textContent = 'Connected (' + d.mqtt_host + ':' + d.mqtt_port + ')'; mel.className = 'status ok'; }
    else { mel.textContent = 'Not configured'; mel.className = 'status info'; }
  });

  api('GET', '/api/setup/identity').then(function(d) {
    var info = document.getElementById('identity-info');
    if (d.exists) {
      info.textContent = 'MUID: ' + d.muid;
    } else {
      info.textContent = 'No identity file. One will be created on first connection.';
    }
  });

  api('GET', '/api/setup/adapters').then(function(d) {
    var el = document.getElementById('adapter-list');
    if (!d.length) { el.textContent = 'No BLE adapters found'; el.className = 'status err'; return; }
    el.innerHTML = '';
    d.forEach(function(a) {
      var powered = a.powered ? '<span style="color:#3dba6f">ON</span>' : '<span style="color:#e05050">OFF</span>';
      el.innerHTML += '<div style="margin-bottom:6px">' + a.path + ' &mdash; ' + a.address + ' ' + powered + '</div>';
    });
  });
}

// -- Scan --
function bleScan() {
  document.getElementById('btn-scan').disabled = true;
  document.getElementById('scan-spinner').style.display = 'inline';
  document.getElementById('device-list').innerHTML = '';
  document.getElementById('pair-row').style.display = 'none';
  _selectedAddr = null;
  logMsg('Scanning for Truma devices (10s)...');

  api('POST', '/api/setup/scan').then(function(d) {
    document.getElementById('btn-scan').disabled = false;
    document.getElementById('scan-spinner').style.display = 'none';
    var list = document.getElementById('device-list');
    if (!d.devices || !d.devices.length) {
      list.innerHTML = '<li>No Truma devices found. Make sure the panel is powered on.</li>';
      logMsg('No devices found');
      return;
    }
    logMsg('Found ' + d.devices.length + ' device(s)');
    list.innerHTML = '';
    d.devices.forEach(function(dev) {
      var badges = '';
      if (dev.paired) badges += '<span class="badge paired">Paired</span>';
      if (dev.connected) badges += '<span class="badge connected">Connected</span>';
      var li = document.createElement('li');
      li.style.cursor = 'pointer';
      li.innerHTML = '<div><span class="device-name">' + dev.name + '</span>' + badges +
        '<br><span class="device-addr">' + dev.address + ' (RSSI: ' + dev.rssi + ')</span></div>';
      if (!dev.paired) {
        li.onclick = function() { selectDevice(dev.address, dev.name); };
      }
      list.appendChild(li);
    });
  }).catch(function(e) {
    document.getElementById('btn-scan').disabled = false;
    document.getElementById('scan-spinner').style.display = 'none';
    logMsg('Scan error: ' + e.message);
  });
}

function selectDevice(addr, name) {
  _selectedAddr = addr;
  document.getElementById('pair-row').style.display = 'flex';
  document.getElementById('pair-status').textContent = 'Selected: ' + name + ' (' + addr + ')';
  document.getElementById('pair-status').className = 'status info';
  logMsg('Selected ' + name + ' for pairing');
}

// -- Pair --
function blePair() {
  if (!_selectedAddr) return;
  var passkey = parseInt(document.getElementById('passkey').value);
  if (!passkey || passkey < 0 || passkey > 999999) {
    document.getElementById('pair-status').textContent = 'Enter a valid 6-digit passkey';
    document.getElementById('pair-status').className = 'status err';
    return;
  }
  document.getElementById('pair-status').textContent = 'Pairing...';
  document.getElementById('pair-status').className = 'status info';
  logMsg('Pairing with ' + _selectedAddr + ' using passkey ' + passkey);

  api('POST', '/api/setup/pair', { address: _selectedAddr, passkey: passkey }).then(function(d) {
    if (d.ok) {
      document.getElementById('pair-status').textContent = d.message;
      document.getElementById('pair-status').className = 'status ok';
      logMsg('Pairing: ' + d.message);
    } else {
      document.getElementById('pair-status').textContent = d.message;
      document.getElementById('pair-status').className = 'status err';
      logMsg('Pairing failed: ' + d.message);
    }
    loadStatus();
  }).catch(function(e) {
    document.getElementById('pair-status').textContent = 'Error: ' + e.message;
    document.getElementById('pair-status').className = 'status err';
  });
}

// -- MQTT --
function saveMqtt() {
  var host = document.getElementById('mqtt-host').value.trim();
  var port = parseInt(document.getElementById('mqtt-port').value) || 1883;
  api('POST', '/api/setup/config', { mqtt_host: host, mqtt_port: port, mqtt_enabled: !!host }).then(function(d) {
    document.getElementById('mqtt-save-status').textContent = 'Saved. Restart service to apply.';
    document.getElementById('mqtt-save-status').className = 'status ok';
    logMsg('MQTT config saved: ' + host + ':' + port);
    loadStatus();
  }).catch(function(e) {
    document.getElementById('mqtt-save-status').textContent = 'Error: ' + e.message;
    document.getElementById('mqtt-save-status').className = 'status err';
  });
}

// -- Unpair --
function unpairDevice() {
  if (!confirm('Unpair from Truma? You will need to re-pair with the passkey from the panel.')) return;
  api('POST', '/api/setup/reset-identity').then(function(d) {
    document.getElementById('identity-reset-status').textContent = d.message;
    document.getElementById('identity-reset-status').className = d.ok ? 'status ok' : 'status err';
    logMsg('Unpair: ' + d.message);
    loadStatus();
  });
}

// -- Restart --
function restartService() {
  if (!confirm('Restart the Truma service? It will reconnect automatically.')) return;
  var el = document.getElementById('restart-status');
  el.textContent = 'Restarting...'; el.className = 'status info';
  logMsg('Restarting service...');
  api('POST', '/api/setup/restart').then(function(d) {
    el.textContent = d.message || 'Restarting'; el.className = 'status ok';
    logMsg('Service restart initiated. Refreshing in 12s...');
    setTimeout(function() { location.reload(); }, 12000);
  }).catch(function(e) {
    el.textContent = 'Error: ' + e.message; el.className = 'status err';
  });
}

loadStatus();
setInterval(loadStatus, 5000);
</script>
</body>
</html>"""


class TrumaRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Truma REST API."""

    # These are set by TrumaRestApi before starting the server
    state_getter = None      # callable() -> dict (from TrumaState.get_status)
    command_sender = None    # callable(topic, param, value) -> (bool, str)
    health_getter = None     # callable() -> dict
    setup_handler = None     # callable(method, path, data) -> dict

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._html_response(WEB_PAGE)
        elif self.path == "/setup":
            self._html_response(SETUP_PAGE)
        elif self.path == "/api/status":
            self._json_response(self.state_getter())
        elif self.path.startswith("/api/status/"):
            section = self.path.split("/")[-1]
            status = self.state_getter()
            if section in status:
                self._json_response(status[section])
            else:
                self._json_response({"error": f"unknown section: {section}"}, 404)
        elif self.path == "/api/health":
            self._json_response(self.health_getter())
        elif self.path.startswith("/api/setup/"):
            self._handle_setup("GET", None)
        else:
            self._json_response({"error": "not found"}, 404)

    def _html_response(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/command":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body)

                topic = data.get("topic")
                param = data.get("param")
                value = data.get("value")

                if not all([topic, param, value is not None]):
                    self._json_response({"error": "missing topic, param, or value"}, 400)
                    return

                ok, msg = self.command_sender(topic, param, int(value))
                if ok:
                    self._json_response({"status": "ok", "message": msg})
                else:
                    self._json_response({"error": msg}, 400)
            except json.JSONDecodeError:
                self._json_response({"error": "invalid JSON"}, 400)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        elif self.path.startswith("/api/setup/"):
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                data = json.loads(body)
                self._handle_setup("POST", data)
            except json.JSONDecodeError:
                self._json_response({"error": "invalid JSON"}, 400)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        else:
            self._json_response({"error": "not found"}, 404)

    def _handle_setup(self, method, data):
        """Route setup API requests."""
        if not self.setup_handler:
            self._json_response({"error": "setup not available"}, 503)
            return
        try:
            result = self.setup_handler(method, self.path, data)
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _json_response(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self._json_response({})

    def log_message(self, format, *args):
        """Suppress default logging — use structured logging instead."""
        pass


class TrumaRestApi:
    """REST API server running in a background thread."""

    def __init__(self, state_getter, command_sender, health_getter, port=8090, setup_handler=None):
        self.port = port
        self._server = None
        self._thread = None
        self._start_time = time.time()

        # Wire up handlers
        TrumaRequestHandler.state_getter = state_getter
        TrumaRequestHandler.command_sender = command_sender
        TrumaRequestHandler.health_getter = health_getter
        TrumaRequestHandler.setup_handler = setup_handler

    def start(self):
        """Start the REST API server in a background thread."""
        HTTPServer.allow_reuse_address = True
        self._server = HTTPServer(("0.0.0.0", self.port), TrumaRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[REST] API listening on port {self.port}")

    def stop(self):
        """Stop the REST API server."""
        if self._server:
            self._server.shutdown()
            print("[REST] API stopped")

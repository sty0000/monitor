from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

from .config import ConfigError
from .config_edit import apply_config_update, apply_profile_update, editable_fields_payload, preview_config_update, preview_profile_update
from .runtime_service import MonitorRuntimeService


def _html_page() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GPU Monitor Dashboard</title>
  <style>
    body { font-family: sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .card { background: #111827; border: 1px solid #334155; border-radius: 10px; padding: 16px; min-width: 260px; }
    .status-card { flex: 1 1 220px; }
    .wide-card { min-width: 0; width: 100%; box-sizing: border-box; }
    .table-wrap { overflow-x: auto; }
    details.advanced { margin-top: 16px; }
    details.advanced > summary { cursor: pointer; color: #cbd5e1; margin-bottom: 12px; }
    .action-bar { margin: 16px 0; }
    button { margin-right: 8px; margin-bottom: 8px; padding: 8px 12px; }
    table { border-collapse: collapse; width: 100%; }
    td, th { border: 1px solid #334155; padding: 8px; text-align: left; }
    .ok { color: #22c55e; }
    .warn { color: #f59e0b; }
    .bad { color: #ef4444; }
    pre { white-space: pre-wrap; word-break: break-word; }
    input { padding: 8px; min-width: 320px; margin-right: 8px; }
    .chart-scroll { overflow-x: auto; padding-bottom: 6px; }
    .chart { width: 960px; height: 240px; max-width: none; background: #020617; border: 1px solid #334155; border-radius: 8px; display: block; }
    .chart-toolbar { margin: 8px 0; }
    .chart-tooltip { min-height: 80px; background: #020617; border: 1px solid #334155; border-radius: 8px; padding: 8px; }
    .muted { color: #94a3b8; }
    @media (max-width: 720px) {
      body { margin: 12px; }
      input { min-width: 0; width: 100%; box-sizing: border-box; margin-bottom: 8px; }
      .card { min-width: 0; width: 100%; box-sizing: border-box; }
      table { white-space: nowrap; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
<h1 id="pageTitle">GPU Monitor Dashboard</h1>
<p>设备名：<strong id="instanceName">-</strong></p>
<p>版本：<strong id="versionText">-</strong></p>
<p>请在下方填入 Bearer Token（如果启用鉴权）。浏览器不会自动保存。</p>
<div>
  <input id="token" type="password" placeholder="Bearer Token" />
  <button id="saveToken">应用 Token</button>
</div>

<div class="row" id="overviewRow">
  <div class="card status-card"><h3>Monitor State</h3><div id="monitorState">-</div></div>
  <div class="card status-card"><h3>Active Alert</h3><div id="activeAlertState">-</div></div>
  <div class="card status-card"><h3>GPU Workload</h3><div id="gpuOverviewState">-</div></div>
  <div class="card status-card"><h3>System / Unified Memory</h3><div id="memoryBody">-</div></div>
</div>

<div class="card wide-card" style="margin-top: 16px;">
  <h3>GPU Snapshot</h3>
  <div class="table-wrap">
    <table>
      <thead><tr><th>GPU</th><th>Util %</th><th id="gpuMemHeader">Mem MB</th><th>Power W</th><th>Temp C</th><th>PIDs</th><th>Device Error</th><th>GPU Error Alert</th></tr></thead>
      <tbody id="gpuBody"></tbody>
    </table>
  </div>
</div>

<div class="card wide-card" style="margin-top: 16px;">
  <h3>Top Processes</h3>
  <div style="margin-bottom: 8px;">
    <button id="sortCpu">Sort by CPU</button>
    <button id="sortMem">Sort by Memory</button>
    <span id="processSortState">CPU</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>PID</th><th>User</th><th>CPU %</th><th>Mem %</th><th>RSS MB</th><th>Command</th><th>Args</th></tr></thead>
      <tbody id="processBody"></tbody>
    </table>
  </div>
</div>

<div class="card wide-card" style="margin-top: 16px;">
  <h3>History Trend</h3>
  <div class="muted" id="historyWindowLabel">最近样本：GPU Util % / Temp C，告警见事件轴。</div>
  <div class="chart-toolbar">
    <label for="historyGpuSelect">GPU:</label>
    <select id="historyGpuSelect"><option value="all">All</option></select>
    <span class="muted">固定尺寸图表；窄窗口可横向滚动。</span>
  </div>
  <div class="chart-scroll">
    <canvas id="historyChart" class="chart" width="960" height="240"></canvas>
  </div>
</div>

<details class="advanced">
  <summary>Advanced controls and diagnostics</summary>
  <div class="row">
    <div class="card"><h3>Notify</h3><div id="notifyState">-</div><div id="lowUsageNotifyState">-</div></div>
    <div class="card"><h3>Alert Silence</h3><pre id="silenceState">-</pre></div>
    <div class="card"><h3>Intervals</h3><div id="intervalState">-</div></div>
    <div class="card"><h3>Channels</h3><div id="routeState">-</div></div>
    <div class="card"><h3>Platform</h3><pre id="platformBody">-</pre></div>
  </div>
  <div class="action-bar">
    <button id="btnEnable">Enable Notify</button>
    <button id="btnDisable">Disable Notify</button>
    <button id="btnLowUsageEnable">Enable Low Usage Notify</button>
    <button id="btnLowUsageDisable">Disable Low Usage Notify</button>
    <button id="btnTest">Send Test</button>
    <button id="btnReload">Reload Config</button>
    <button id="btnAckAlert" title="Acknowledge current alert: stop repeating this active alert until it clears or is reset.">Acknowledge Current Alert</button>
    <button id="btnSilenceAlert" title="Silence current alert for one hour.">Silence Current Alert 1h</button>
    <button id="btnSilenceToday" title="Silence current alert until local end of day.">Silence Today</button>
    <button id="btnSilencePermanent" title="Silence current alert until cleared manually.">Silence Permanent</button>
    <button id="btnClearSilence" title="Clear acknowledge and silence state for current alert.">Clear Ack/Silence</button>
  </div>
  <div class="card" style="margin-top: 16px;"><h3>Control Result</h3><pre id="controlResult">No action yet.</pre></div>
  <div class="card" style="margin-top: 16px;"><h3>Alert Timeline</h3><pre id="timelineBody">-</pre></div>
  <div class="card" style="margin-top: 16px;"><h3>Notifier Health</h3><pre id="notifierHealthBody">-</pre></div>
  <div class="card" style="margin-top: 16px;"><h3>Health</h3><pre id="healthBody">-</pre></div>
  <div class="card" style="margin-top: 16px;" id="configEditorCard">
    <h3>Config Editor</h3>
    <div class="muted">Only whitelisted low-risk fields are shown. token, host, port, notifier secrets, logging paths and systemd paths are not editable.</div>
    <div id="configEditorFields">Loading editable fields...</div>
    <div style="margin-top: 8px;">
      <button id="btnConfigPreview">Preview Config Change</button>
      <button id="btnConfigApply" disabled>Apply Previewed Change</button>
    </div>
    <div class="muted" id="configApplyHint">Preview must pass before apply. Apply creates a backup and reloads config.</div>
    <pre id="configPreviewBody">-</pre>
  </div>
  <div class="card" style="margin-top: 16px;"><h3>Recent Events</h3><pre id="events">-</pre></div>
</details>

<script>
var bearerToken = '';
var processSortKey = 'cpu';
var editableConfigFields = [];
var lastConfigPreviewOk = false;

function buildHeaders() {
  var headers = { 'Content-Type': 'application/json' };
  if (bearerToken) {
    headers.Authorization = 'Bearer ' + bearerToken;
  }
  return headers;
}


function setControlResult(message, payload) {
  var detail = payload === undefined ? '' : '\\n' + JSON.stringify(payload, null, 2);
  document.getElementById('controlResult').textContent = message + detail;
}

function runAction(label, url, body) {
  setControlResult(label + ' ...');
  return api(url, 'POST', body || {}).then(function (payload) {
    var ok = payload && payload.ok !== false;
    setControlResult(label + (ok ? ' succeeded' : ' returned an error'), payload);
    return refresh();
  }).catch(function (err) {
    setControlResult(label + ' failed: ' + err.message);
  });
}

function api(url, method, body) {
  if (!method) {
    method = 'GET';
  }
  return fetch(url, {
    method: method,
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : null,
  }).then(function (resp) {
    if (!resp.ok) {
      return resp.text().catch(function () { return ''; }).then(function (text) {
        throw new Error('HTTP ' + resp.status + ': ' + text);
      });
    }
    var type = resp.headers.get('content-type') || '';
    if (type.indexOf('application/json') >= 0) {
      return resp.json();
    }
    return resp.text();
  });
}

function fmtEvents(events) {
  return events.map(function (event) {
    var extra = event.extra ? '\\n' + JSON.stringify(event.extra, null, 2) : '';
    return '[' + event.ts + '] ' + event.kind + ': ' + event.message + extra;
  }).join('\\n\\n');
}


var historyGpuSelection = 'all';
var latestHistory = { points: [], events: [] };
var historyPointPositions = [];

function formatTimeLabel(timestamp) {
  if (!timestamp) { return '-'; }
  var date = new Date(timestamp);
  if (isNaN(date.getTime())) { return String(timestamp); }
  return date.toLocaleTimeString();
}

function collectHistoryGpuIds(points) {
  var seen = {};
  var ids = [];
  points.forEach(function (point) {
    (point.gpus || []).forEach(function (gpu) {
      if (gpu.index === undefined || gpu.index === null) { return; }
      var key = String(gpu.index);
      if (!seen[key]) {
        seen[key] = true;
        ids.push(gpu.index);
      }
    });
  });
  return ids.sort(function (a, b) { return Number(a) - Number(b); });
}

function syncHistoryGpuSelect(points) {
  var select = document.getElementById('historyGpuSelect');
  if (!select) { return; }
  var gpuIds = collectHistoryGpuIds(points);
  var previous = historyGpuSelection || select.value || 'all';
  var options = ['<option value="all">All</option>'].concat(gpuIds.map(function (gpuId) {
    return '<option value="' + gpuId + '">GPU' + gpuId + '</option>';
  }));
  select.innerHTML = options.join('');
  var values = ['all'].concat(gpuIds.map(String));
  historyGpuSelection = values.indexOf(String(previous)) >= 0 ? String(previous) : 'all';
  select.value = historyGpuSelection;
}

function gpuAtPoint(point, gpuIndex) {
  return ((point.gpus || []).filter(function (item) { return String(item.index) === String(gpuIndex); })[0]) || null;
}

function drawSeries(ctx, data, color) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  var started = false;
  data.forEach(function (point) {
    if (!point) { return; }
    if (!started) { ctx.moveTo(point.x, point.y); started = true; }
    else { ctx.lineTo(point.x, point.y); }
  });
  if (started) { ctx.stroke(); }
  data.forEach(function (point) {
    if (!point) { return; }
    ctx.beginPath();
    ctx.arc(point.x, point.y, 2.5, 0, Math.PI * 2);
    ctx.fill();
  });
}

function drawLegend(ctx, items, width) {
  var x = width - 12;
  var y = 18;
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  items.forEach(function (item) {
    var textWidth = ctx.measureText(item.label).width;
    var boxX = x - textWidth - 22;
    ctx.fillStyle = item.color;
    ctx.fillRect(boxX, y - 5, 10, 10);
    ctx.fillStyle = '#e2e8f0';
    ctx.fillText(item.label, x, y);
    y += 18;
  });
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
}

function historyScale(value, top, bottom) {
  return bottom - Math.max(0, Math.min(100, Number(value))) / 100 * (bottom - top);
}

function drawHistory(history) {
  latestHistory = history || { points: [], events: [] };
  var canvas = document.getElementById('historyChart');
  if (!canvas) { return; }
  var ctx = canvas.getContext('2d');
  var width = canvas.width;
  var height = canvas.height;
  var left = 48;
  var right = width - 16;
  var top = 22;
  var bottom = height - 34;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#020617';
  ctx.fillRect(0, 0, width, height);
  var points = (history && history.points) || [];
  syncHistoryGpuSelect(points);
  historyPointPositions = [];
  var windowLabel = document.getElementById('historyWindowLabel');
  if (windowLabel) {
    windowLabel.textContent = points.length ? ('Showing last ' + points.length + ' samples, ' + formatTimeLabel(points[0].timestamp) + ' - ' + formatTimeLabel(points[points.length - 1].timestamp)) : 'No history samples yet.';
  }
  if (!points.length) {
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('No history yet', left, 42);
    return;
  }

  ctx.strokeStyle = '#334155';
  ctx.fillStyle = '#94a3b8';
  ctx.lineWidth = 1;
  [0, 25, 50, 75, 100].forEach(function (tick) {
    var y = historyScale(tick, top, bottom);
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.fillText(String(tick), 12, y + 4);
  });
  [0, 0.5, 1].forEach(function (ratio) {
    var x = left + ratio * (right - left);
    var index = Math.round(ratio * (points.length - 1));
    ctx.fillText(formatTimeLabel(points[index].timestamp), Math.max(left, x - 34), height - 10);
  });

  function xForIndex(i) {
    return points.length === 1 ? left : left + (i / (points.length - 1)) * (right - left);
  }
  historyPointPositions = points.map(function (point, i) { return { x: xForIndex(i), point: point, index: i }; });
  var gpuIds = collectHistoryGpuIds(points);
  var colors = ['#38bdf8', '#a78bfa', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#f97316', '#eab308'];
  var selection = historyGpuSelection || 'all';
  if (selection === 'all') {
    gpuIds.forEach(function (gpuId, colorIndex) {
      var data = points.map(function (point, i) {
        var gpu = gpuAtPoint(point, gpuId);
        var value = gpu && gpu.utilization_gpu;
        if (value === null || value === undefined) { return null; }
        return { x: xForIndex(i), y: historyScale(value, top, bottom) };
      });
      drawSeries(ctx, data, colors[colorIndex % colors.length]);
    });
    drawLegend(ctx, gpuIds.map(function (gpuId, colorIndex) {
      return { color: colors[colorIndex % colors.length], label: 'GPU' + gpuId + ' util' };
    }), width);
  } else {
    var singleGpuSeries = [
      { key: 'utilization_gpu', color: '#38bdf8', label: 'GPU' + selection + ' Util %' },
      { key: 'temperature_c', color: '#f97316', label: 'GPU' + selection + ' Temp C' },
      { key: 'power_draw_w', color: '#22c55e', label: 'GPU' + selection + ' Power W' },
    ];
    singleGpuSeries.forEach(function (spec) {
      var data = points.map(function (point, i) {
        var gpu = gpuAtPoint(point, selection);
        var value = gpu && gpu[spec.key];
        if (value === null || value === undefined) { return null; }
        return { x: xForIndex(i), y: historyScale(value, top, bottom) };
      });
      drawSeries(ctx, data, spec.color);
    });
    drawLegend(ctx, singleGpuSeries, width);
  }

}

function renderTimeline(history) {
  var events = ((history && history.events) || []).filter(function (event) {
    return event.kind === 'notify_sent' || event.kind === 'error' || event.kind === 'system';
  }).slice(0, 20);
  document.getElementById('timelineBody').textContent = events.map(function (event) {
    return '[' + event.ts + '] ' + event.kind + ': ' + event.message;
  }).join('\\n') || '-';
}

function parseConfigValue(raw, typeName) {
  if (typeName === 'boolean') {
    return raw === true || raw === 'true';
  }
  if (typeName === 'integer') {
    return parseInt(raw, 10);
  }
  if (typeName === 'integer|null') {
    return raw === '' || raw === 'null' ? null : parseInt(raw, 10);
  }
  if (typeName === 'number') {
    return Number(raw);
  }
  if (typeName === 'list[string]') {
    return raw.split(',').map(function (item) { return item.trim(); }).filter(Boolean);
  }
  return raw;
}

function controlForField(field) {
  var id = 'configField_' + field.path.replace(/[^a-zA-Z0-9]/g, '_');
  var label = '<label for="' + id + '"><strong>' + field.path + '</strong><br><span class="muted">' + field.description + '</span></label>';
  if (field.type === 'boolean') {
    return '<div><input id="' + id + '" data-config-path="' + field.path + '" data-config-type="' + field.type + '" type="checkbox" /> ' + label + '</div>';
  }
  if (field.type === 'enum') {
    return '<div>' + label + '<br><select id="' + id + '" data-config-path="' + field.path + '" data-config-type="' + field.type + '"><option value="any">any</option><option value="all">all</option><option value="majority">majority</option><option value="selected_primary">selected_primary</option></select></div>';
  }
  var inputType = field.type === 'number' || field.type === 'integer' || field.type === 'integer|null' ? 'number' : 'text';
  var placeholder = field.type === 'list[string]' ? 'wecom,telegram' : field.type;
  return '<div>' + label + '<br><input id="' + id + '" data-config-path="' + field.path + '" data-config-type="' + field.type + '" type="' + inputType + '" placeholder="' + placeholder + '" /></div>';
}

function loadConfigEditor() {
  api('/api/config/editable').then(function (payload) {
    editableConfigFields = payload.fields || [];
    document.getElementById('configEditorFields').innerHTML = editableConfigFields.map(controlForField).join('') || 'No editable fields';
  }).catch(function (err) {
    document.getElementById('configEditorFields').textContent = 'Load editable fields failed: ' + err.message;
  });
}

function collectConfigUpdates() {
  var updates = {};
  Array.prototype.forEach.call(document.querySelectorAll('[data-config-path]'), function (input) {
    var path = input.getAttribute('data-config-path');
    var typeName = input.getAttribute('data-config-type');
    var raw = input.type === 'checkbox' ? input.checked : input.value;
    if (input.type !== 'checkbox' && String(raw).trim() === '') { return; }
    updates[path] = parseConfigValue(raw, typeName);
  });
  return updates;
}

function previewConfigChange() {
  lastConfigPreviewOk = false;
  document.getElementById('btnConfigApply').disabled = true;
  var updates = collectConfigUpdates();
  api('/api/config/preview', 'POST', { updates: updates }).then(function (payload) {
    lastConfigPreviewOk = !!payload.ok;
    document.getElementById('btnConfigApply').disabled = !lastConfigPreviewOk;
    document.getElementById('configPreviewBody').textContent = JSON.stringify(payload.changes || [], null, 2) + '\\n\\nvalidation:\\n' + JSON.stringify(payload.validation || {}, null, 2) + '\\n\\ndiff:\\n' + (payload.diff || '');
  }).catch(function (err) {
    document.getElementById('configPreviewBody').textContent = 'Preview failed; apply is disabled.\\n' + err.message;
  });
}

function applyConfigChange() {
  if (!lastConfigPreviewOk) {
    document.getElementById('configPreviewBody').textContent = 'Preview must pass before apply.';
    return;
  }
  if (!window.confirm('Apply previewed config change? A backup will be created before reload.')) {
    return;
  }
  api('/api/config/apply', 'POST', { updates: collectConfigUpdates() }).then(function (payload) {
    lastConfigPreviewOk = false;
    document.getElementById('btnConfigApply').disabled = true;
    document.getElementById('configPreviewBody').textContent = 'Apply result:\\n' + JSON.stringify(payload, null, 2) + '\\n\\nbackup_path: ' + (payload.backup_path || '-');
    return refresh();
  }).catch(function (err) {
    lastConfigPreviewOk = false;
    document.getElementById('btnConfigApply').disabled = true;
    document.getElementById('configPreviewBody').textContent = 'Apply failed. If reload failed, backend reports rolled_back=true.\\n' + err.message;
  });
}

function render(status, health, history) {
  var instanceName = ((status.config_summary || {}).monitor || {}).instance_name || '-';
  document.getElementById('instanceName').textContent = instanceName;
  document.getElementById('pageTitle').textContent = 'GPU Monitor Dashboard - ' + instanceName;
  document.getElementById('versionText').textContent = status.version || '-';
  document.title = 'GPU Monitor Dashboard - ' + instanceName;
  var state = status.monitor_state || '-';
  var cls = state === 'ACTIVE' ? 'ok' : ((state.indexOf('ALERT') >= 0 || state === 'ERROR') ? 'bad' : 'warn');
  document.getElementById('monitorState').innerHTML = '<span class="' + cls + '">' + state + '</span><div>' + (status.reason || '') + '</div>';
  var activeAlert = status.active_alert || (state.indexOf('ALERT') >= 0 ? state : 'None');
  var activeAlertClass = activeAlert === 'None' ? 'ok' : 'bad';
  document.getElementById('activeAlertState').innerHTML = '<span class="' + activeAlertClass + '">' + activeAlert + '</span>';
  document.getElementById('notifyState').innerHTML = status.notify_enabled ? '<span class="ok">ON</span>' : '<span class="warn">OFF</span>';
  document.getElementById('lowUsageNotifyState').innerHTML = 'Low usage: ' + (status.low_usage_notify_enabled ? '<span class="ok">ON</span>' : '<span class="warn">OFF</span>');
  document.getElementById('intervalState').textContent = status.interval_seconds + 's / cooldown ' + status.cooldown_minutes + 'm / global ' + status.min_interval_minutes + 'm';
  document.getElementById('routeState').textContent = (status.notifier_order_active || []).join(' -> ') || '(none)';
  document.getElementById('silenceState').textContent = 'Ack: ' + JSON.stringify(status.acknowledged_alerts || []) + '\\nTemp: ' + JSON.stringify(status.silenced_alerts_until || {}) + '\\nPermanent: ' + JSON.stringify(status.silenced_alerts_permanent || []);

  var platformSummary = ((status.sample || {}).platform_summary) || status.platform_summary || {};
  var systemMemory = (status.sample && status.sample.system_memory) || {};
  var isUnifiedMemoryPlatform = platformSummary.profile === 'dgx_spark';
  var memoryLabel = isUnifiedMemoryPlatform ? 'GPU/Unified Mem MB' : 'Mem MB';
  document.getElementById('gpuMemHeader').textContent = memoryLabel;
  document.getElementById('platformBody').textContent = JSON.stringify(platformSummary, null, 2);
  document.getElementById('memoryBody').innerHTML = systemMemory.ok ? ('used ' + systemMemory.used_mb + 'MB / total ' + systemMemory.total_mb + 'MB (' + systemMemory.used_percent + '%), available ' + systemMemory.available_mb + 'MB') : (systemMemory.error || 'N/A');

  var mutedGpuIds = status.muted_gpu_error_ids || [];
  var gpus = (status.sample && status.sample.gpus) || [];
  function fmtValue(value) {
    return value === null || value === undefined ? 'N/A' : value;
  }
  function formatGpuMemory(gpu) {
    if (gpu.memory_used_mb !== null && gpu.memory_used_mb !== undefined) {
      return fmtValue(gpu.memory_used_mb);
    }
    if (isUnifiedMemoryPlatform && systemMemory.ok) {
      return systemMemory.used_mb + ' / ' + systemMemory.total_mb + ' unified';
    }
    return 'N/A';
  }
  var rows = gpus.map(function (gpu) {
    var muted = mutedGpuIds.indexOf(gpu.index) >= 0;
    var errorText = gpu.device_error || '';
    var buttonText = muted ? 'Enable GPU error alert' : 'Mute GPU error alert';
    var button = '<button data-gpu-id="' + gpu.index + '" data-muted="' + muted + '" class="gpuMuteBtn">' + buttonText + '</button>';
    return '<tr><td>' + gpu.index + '</td><td>' + fmtValue(gpu.utilization_gpu) + '</td><td>' + formatGpuMemory(gpu) + '</td><td>' + fmtValue(gpu.power_draw_w) + '</td><td>' + fmtValue(gpu.temperature_c) + '</td><td>' + ((gpu.compute_pids || []).join(',')) + '</td><td class="' + (errorText ? 'bad' : 'ok') + '">' + (errorText || 'OK') + '</td><td>' + button + '</td></tr>';
  }).join('');
  document.getElementById('gpuBody').innerHTML = rows || '<tr><td colspan="8">No data</td></tr>';
  var busyCount = gpus.filter(function (gpu) { return Number(gpu.utilization_gpu || 0) > 0; }).length;
  var hotCount = gpus.filter(function (gpu) { return Number(gpu.temperature_c || 0) >= 80; }).length;
  var processCount = gpus.reduce(function (total, gpu) { return total + ((gpu.compute_pids || []).length); }, 0);
  document.getElementById('gpuOverviewState').innerHTML = gpus.length
    ? (busyCount + '/' + gpus.length + ' busy, ' + hotCount + ' hot, ' + processCount + ' GPU process pid(s)')
    : 'No GPU data';
  Array.prototype.forEach.call(document.getElementsByClassName('gpuMuteBtn'), function (button) {
    button.onclick = function () {
      var gpuId = Number(button.getAttribute('data-gpu-id'));
      var currentlyMuted = button.getAttribute('data-muted') === 'true';
      api('/api/gpu-error-mute', 'POST', { gpu_id: gpuId, muted: !currentlyMuted }).then(refresh);
    };
  });
  var processUsage = (status.sample && status.sample.process_usage) || {};
  var processRows = (processSortKey === 'memory' ? processUsage.top_memory : processUsage.top_cpu) || [];
  document.getElementById('processSortState').textContent = processSortKey === 'memory' ? 'Memory' : 'CPU';
  document.getElementById('processBody').innerHTML = processRows.map(function (proc) {
    return '<tr><td>' + proc.pid + '</td><td>' + proc.user + '</td><td>' + fmtValue(proc.cpu_percent) + '</td><td>' + fmtValue(proc.memory_percent) + '</td><td>' + fmtValue(proc.rss_mb) + '</td><td>' + proc.command + '</td><td>' + proc.args + '</td></tr>';
  }).join('') || '<tr><td colspan="7">' + (processUsage.error || 'No data') + '</td></tr>';

  document.getElementById('notifierHealthBody').textContent = JSON.stringify(status.notifier_health || {}, null, 2);
  document.getElementById('healthBody').textContent = JSON.stringify(health, null, 2);
  document.getElementById('events').textContent = fmtEvents(status.events || []);
  drawHistory(history);
  renderTimeline(history);
}

function refresh() {
  return Promise.all([api('/api/status'), api('/api/health'), api('/api/history?limit=240')])
    .then(function (results) {
      render(results[0], results[1], results[2]);
    })
    .catch(function (err) {
      document.getElementById('events').textContent = '拉取状态失败: ' + err.message;
    });
}

document.getElementById('saveToken').onclick = function () {
  bearerToken = document.getElementById('token').value.trim();
  refresh();
};
document.getElementById('btnEnable').onclick = function () { runAction('Enable notify', '/api/notify', { enabled: true }); };
document.getElementById('btnDisable').onclick = function () { runAction('Disable notify', '/api/notify', { enabled: false }); };
document.getElementById('btnLowUsageEnable').onclick = function () { runAction('Enable low usage notify', '/api/low-usage-notify', { enabled: true }); };
document.getElementById('btnLowUsageDisable').onclick = function () { runAction('Disable low usage notify', '/api/low-usage-notify', { enabled: false }); };
document.getElementById('btnTest').onclick = function () { runAction('Send test notification', '/api/test-notify', {}); };
document.getElementById('btnReload').onclick = function () { runAction('Reload config', '/api/reload-config', {}); };
document.getElementById('btnAckAlert').onclick = function () { runAction('Acknowledge current alert', '/api/acknowledge-alert', {}); };
document.getElementById('btnSilenceAlert').onclick = function () { runAction('Silence current alert for 1h', '/api/alert-silence', { mode: '1h' }); };
document.getElementById('btnSilenceToday').onclick = function () { runAction('Silence current alert for today', '/api/alert-silence', { mode: 'today' }); };
document.getElementById('btnSilencePermanent').onclick = function () { runAction('Silence current alert permanently', '/api/alert-silence', { mode: 'permanent' }); };
document.getElementById('btnClearSilence').onclick = function () { runAction('Clear acknowledge/silence', '/api/alert-silence', { mode: 'clear' }); };
document.getElementById('sortCpu').onclick = function () { processSortKey = 'cpu'; refresh(); };
document.getElementById('sortMem').onclick = function () { processSortKey = 'memory'; refresh(); };
document.getElementById('historyGpuSelect').onchange = function () { historyGpuSelection = this.value; drawHistory(latestHistory); };
document.getElementById('btnConfigPreview').onclick = previewConfigChange;
document.getElementById('btnConfigApply').onclick = applyConfigChange;

loadConfigEditor();
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def _ensure_auth(runtime: MonitorRuntimeService, write: bool) -> Response | None:
    if runtime.is_authorized(request.headers.get("Authorization"), write=write):
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def create_app(runtime: MonitorRuntimeService) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        return _html_page()

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status=204)

    @app.get("/api/status")
    def status() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        return jsonify(runtime.get_status())


    @app.get("/api/history")
    def history() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        try:
            limit = int(request.args.get("limit", str(runtime.config.history.query_default_limit)))
        except ValueError:
            limit = runtime.config.history.query_default_limit
        try:
            gpu = int(request.args["gpu"]) if "gpu" in request.args else None
        except ValueError:
            return jsonify({"ok": False, "error": "gpu must be an integer"}), 400
        try:
            bucket_seconds = int(request.args["bucket"]) if "bucket" in request.args else None
        except ValueError:
            return jsonify({"ok": False, "error": "bucket must be an integer number of seconds"}), 400
        since = request.args.get("since", request.args.get("from", ""))
        try:
            return jsonify(
                runtime.get_history(
                    limit=limit,
                    since=since,
                    gpu=gpu,
                    state=request.args.get("state", ""),
                    bucket_seconds=bucket_seconds,
                    metric=request.args.get("metric", "utilization_gpu"),
                    agg=request.args.get("agg", "avg"),
                )
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.get("/api/health")
    def health() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        return jsonify(runtime.get_health())

    @app.get("/metrics")
    def metrics() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        return Response(runtime.get_metrics_payload(), mimetype="text/plain; version=0.0.4; charset=utf-8")

    @app.post("/api/notify")
    def set_notify() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", True))
        runtime.set_notify_enabled(enabled)
        return jsonify({"ok": True, "notify_enabled": enabled})

    @app.post("/api/low-usage-notify")
    def set_low_usage_notify() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", True))
        runtime.set_low_usage_notify_enabled(enabled)
        return jsonify({"ok": True, "low_usage_notify_enabled": enabled})

    @app.post("/api/gpu-error-mute")
    def set_gpu_error_mute() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            gpu_id = int(payload["gpu_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "gpu_id is required"}), 400
        muted = bool(payload.get("muted", True))
        muted_gpu_error_ids = runtime.set_gpu_error_muted(gpu_id, muted)
        return jsonify({"ok": True, "gpu_id": gpu_id, "muted": muted, "muted_gpu_error_ids": muted_gpu_error_ids})

    @app.post("/api/test-notify")
    def test_notify() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            sent = runtime.send_test_notification(payload.get("channel"))
            return jsonify({"ok": True, "sent": sent, "channel": payload.get("channel")})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500


    @app.post("/api/acknowledge-alert")
    def acknowledge_alert() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        alert_key = str(payload.get("alert_key") or "")
        silence_minutes = float(payload.get("silence_minutes") or 0)
        return jsonify(runtime.acknowledge_alert(alert_key=alert_key, silence_minutes=silence_minutes))

    @app.post("/api/alert-silence")
    def alert_silence() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        alert_key = str(payload.get("alert_key") or "")
        mode = str(payload.get("mode") or "")
        return jsonify(runtime.silence_alert(alert_key=alert_key, mode=mode))

    @app.post("/api/reload-config")
    def reload_config() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        result = runtime.reload_config()
        return jsonify(result)

    @app.get("/api/config/editable")
    def config_editable() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        return jsonify(editable_fields_payload())

    @app.post("/api/config/preview")
    def config_preview() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            result = preview_config_update(runtime.config_path, payload)
        except ConfigError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result), 200 if result.get("ok") else 400

    @app.post("/api/config/apply")
    def config_apply() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            result = apply_config_update(runtime.config_path, payload, runtime.reload_config)
        except ConfigError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result), 200 if result.get("ok") else 400

    @app.post("/api/config/profile-preview")
    def config_profile_preview() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            result = preview_profile_update(runtime.config_path, str(payload.get("profile") or ""))
        except ConfigError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result), 200 if result.get("ok") else 400

    @app.post("/api/config/profile-apply")
    def config_profile_apply() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            result = apply_profile_update(runtime.config_path, str(payload.get("profile") or ""), runtime.reload_config)
        except ConfigError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result), 200 if result.get("ok") else 400

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU monitor dashboard web UI")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to YAML config file")
    parser.add_argument("--host", default=None, help="Bind host (overrides config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (overrides config)")
    return parser.parse_args()


def main() -> int:
    from .service import run_dashboard

    args = parse_args()
    return run_dashboard(args.config, host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())

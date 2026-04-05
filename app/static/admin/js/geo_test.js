let currentTaskId = null;
let eventSource = null;

const byId = (id) => document.getElementById(id);

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  return (bytes / 1024).toFixed(1) + ' KB';
}

function buildGeoPayload() {
  const countriesRaw = byId('cfg-countries').value.trim();
  const countries = countriesRaw
    ? countriesRaw.split(',').map(c => c.trim().toLowerCase()).filter(Boolean)
    : [];

  return {
    geo_test: {
      proxy_api_key: byId('cfg-proxy-api-key').value.trim(),
      proxy_sub_user_id: parseInt(byId('cfg-proxy-sub-user-id').value) || 0,
      prompt: byId('cfg-prompt').value.trim(),
      images_per_country: parseInt(byId('cfg-images-per-country').value) || 4,
      pass_threshold: parseInt(byId('cfg-pass-threshold').value) || 2,
      countries: countries,
    }
  };
}

/* ---------- Config load / save ---------- */

async function loadConfig() {
  const apiKey = await ensureAdminKey();
  if (!apiKey) return;
  try {
    const res = await fetch('/v1/admin/config', { headers: buildAuthHeaders(apiKey) });
    if (!res.ok) return;
    const cfg = await res.json();
    const gt = cfg.geo_test || {};
    byId('cfg-proxy-api-key').value = gt.proxy_api_key || '';
    byId('cfg-proxy-sub-user-id').value = gt.proxy_sub_user_id || '';
    byId('cfg-prompt').value = gt.prompt || '';
    byId('cfg-images-per-country').value = gt.images_per_country || 4;
    byId('cfg-pass-threshold').value = gt.pass_threshold || 2;
    const countries = gt.countries || [];
    byId('cfg-countries').value = Array.isArray(countries) ? countries.join(',') : '';
  } catch (e) {
    console.error('Failed to load config:', e);
  }
}

async function saveConfig() {
  const apiKey = await ensureAdminKey();
  if (!apiKey) return false;

  const payload = buildGeoPayload();

  try {
    const res = await fetch('/v1/admin/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...buildAuthHeaders(apiKey) },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      showToast('Config saved', 'success');
      return true;
    } else {
      const err = await res.text();
      showToast('Failed to save config: ' + err, 'error');
      return false;
    }
  } catch (e) {
    showToast('Error saving config: ' + e.message, 'error');
    return false;
  }
}

/* ---------- Test execution ---------- */

async function startTest() {
  const apiKey = await ensureAdminKey();
  if (!apiKey) return;

  // Client-side validation
  const payload = buildGeoPayload();
  const gt = payload.geo_test;
  if (!gt.prompt) {
    showToast('Please enter a test prompt', 'error');
    return;
  }
  if (!gt.proxy_api_key) {
    showToast('Please enter the proxy API key', 'error');
    return;
  }
  if (!gt.proxy_sub_user_id) {
    showToast('Please enter the proxy sub-user ID', 'error');
    return;
  }
  if (!gt.countries || gt.countries.length === 0) {
    showToast('Please enter at least one country code', 'error');
    return;
  }

  // Reset UI
  byId('progress-panel').classList.remove('hidden');
  byId('summary-panel').classList.add('hidden');
  byId('results-panel').classList.add('hidden');
  byId('results-body').innerHTML = '';
  byId('live-feed').innerHTML = '';
  byId('progress-bar').style.width = '0%';
  byId('progress-processed').textContent = '0';
  byId('progress-ok').textContent = '0';
  byId('progress-fail').textContent = '0';
  byId('btn-run').disabled = true;
  byId('btn-run').classList.add('opacity-50');
  byId('btn-cancel').classList.remove('hidden');

  try {
    // Send config in the request body so the server saves + uses it atomically
    const res = await fetch('/v1/admin/geo-test/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...buildAuthHeaders(apiKey) },
      body: JSON.stringify(payload),
    });

    let data;
    try {
      data = await res.json();
    } catch (parseErr) {
      const text = await res.text().catch(() => '');
      showToast('Server error (' + res.status + '): ' + (text || 'invalid response'), 'error');
      resetButtons();
      return;
    }

    if (!res.ok || !data.task_id) {
      showToast(data.detail || data.error || ('Server error: HTTP ' + res.status), 'error');
      resetButtons();
      return;
    }

    currentTaskId = data.task_id;
    byId('progress-total').textContent = data.total;
    connectSSE(data.task_id);
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
    resetButtons();
  }
}

async function cancelTest() {
  if (!currentTaskId) return;
  const apiKey = await ensureAdminKey();
  if (!apiKey) return;
  try {
    await fetch(`/v1/admin/geo-test/${currentTaskId}/cancel`, {
      method: 'POST',
      headers: buildAuthHeaders(apiKey),
    });
  } catch (e) {
    console.error('Cancel failed:', e);
  }
}

function connectSSE(taskId) {
  if (eventSource) eventSource.close();

  // Get raw app_key for SSE query param
  getStoredAppKey().then(key => {
    const url = `/v1/admin/geo-test/${taskId}/stream?app_key=${encodeURIComponent(key)}`;
    eventSource = new EventSource(url);

    eventSource.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        handleEvent(event);
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      eventSource = null;
      resetButtons();
    };
  });
}

function handleEvent(event) {
  const type = event.type;

  if (type === 'snapshot' || type === 'progress') {
    const processed = event.processed || 0;
    const total = event.total || 1;
    const ok = event.ok || 0;
    const fail = event.fail || 0;
    const pct = Math.round((processed / total) * 100);

    byId('progress-bar').style.width = pct + '%';
    byId('progress-processed').textContent = processed;
    byId('progress-total').textContent = total;
    byId('progress-ok').textContent = ok;
    byId('progress-fail').textContent = fail;

    // Live feed entry
    if (type === 'progress' && event.detail) {
      const d = event.detail;
      const cc = (d.country || '').toUpperCase();
      const isPass = d.pass;
      const cls = isPass ? 'pass' : (d.status === 'NO_PROXY' ? 'skip' : 'fail');
      const icon = isPass ? 'PASS' : (d.status === 'NO_PROXY' ? 'SKIP' : 'FAIL');
      const line = document.createElement('div');
      line.className = `feed-line ${cls}`;
      line.textContent = `[${cc}] ${icon} - score=${d.score}, pass_images=${d.pass_count}/${d.total}, avg=${formatBytes(d.avg_size)}`;
      byId('live-feed').appendChild(line);
      byId('live-feed').scrollTop = byId('live-feed').scrollHeight;
    }
  }

  if (type === 'done') {
    if (eventSource) { eventSource.close(); eventSource = null; }
    resetButtons();
    renderResults(event.result);
  }

  if (type === 'error') {
    if (eventSource) { eventSource.close(); eventSource = null; }
    resetButtons();
    showToast('Test failed: ' + (event.error || 'unknown'), 'error');
  }

  if (type === 'cancelled') {
    if (eventSource) { eventSource.close(); eventSource = null; }
    resetButtons();
    showToast('Test cancelled', 'warning');
  }
}

function renderResults(result) {
  if (!result) return;
  const summary = result.summary || {};
  const ranked = result.ranked || [];

  // Summary
  byId('summary-panel').classList.remove('hidden');
  byId('sum-total').textContent = summary.total_countries || 0;
  byId('sum-passed').textContent = summary.passed || 0;
  byId('sum-failed').textContent = summary.failed || 0;
  byId('sum-duration').textContent = (result.duration_sec || 0) + 's';

  // Table
  byId('results-panel').classList.remove('hidden');
  const tbody = byId('results-body');
  tbody.innerHTML = '';

  ranked.forEach((r, i) => {
    const cc = (r.country || '').toUpperCase();
    const isPass = r.pass;
    const passCount = r.pass_count || 0;
    const total = r.total || 0;
    const okCount = r.ok_count || 0;

    let resultClass, resultText;
    if (r.status === 'NO_PROXY') {
      resultClass = 'result-skip';
      resultText = 'NO PROXY';
    } else if (isPass) {
      resultClass = 'result-pass';
      resultText = 'PASS';
    } else {
      resultClass = 'result-fail';
      resultText = 'FAIL';
    }

    const tr = document.createElement('tr');
    tr.className = 'border-b border-[var(--border)] hover:bg-[var(--accents-1)]';
    tr.innerHTML = `
      <td class="px-4 py-3 text-[var(--accents-4)]">${i + 1}</td>
      <td class="px-4 py-3 font-mono font-semibold">${cc}</td>
      <td class="px-4 py-3">${r.score || 0}</td>
      <td class="px-4 py-3 font-semibold">${passCount} / ${total}</td>
      <td class="px-4 py-3">${okCount} / ${total}</td>
      <td class="px-4 py-3 font-mono">${formatBytes(r.avg_size)}</td>
      <td class="px-4 py-3 ${resultClass}">${resultText}</td>
    `;
    tbody.appendChild(tr);
  });
}

function resetButtons() {
  byId('btn-run').disabled = false;
  byId('btn-run').classList.remove('opacity-50');
  byId('btn-cancel').classList.add('hidden');
  currentTaskId = null;
}

/* ---------- Init ---------- */

document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
});

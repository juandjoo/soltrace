// Register center-text plugin for doughnut charts (used by all donut charts on dashboard)
const _centerPlugin = {
  id: 'centerText',
  afterDraw(chart) {
    const ct = chart.options?.plugins?.centerText;
    if (!ct) return;
    const {ctx, chartArea: {top, bottom, left, right}} = chart;
    const cx = (left + right) / 2, cy = (top + bottom) / 2;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `bold ${ct.size || 13}px sans-serif`;
    ctx.fillStyle = ct.color || Chart.defaults.color;
    ctx.fillText(ct.line1 || '', cx, ct.line2 ? cy - 9 : cy);
    if (ct.line2) {
      ctx.font = `${(ct.size || 13) - 1}px sans-serif`;
      ctx.fillStyle = ct.subColor || Chart.defaults.color;
      ctx.fillText(ct.line2, cx, cy + 9);
    }
    ctx.restore();
  }
};
if (!Chart.registry.plugins.get('centerText')) Chart.register(_centerPlugin);

const METRIC_LABEL = {fail_rate:'전송 실패율', throughput:'전송 속도', login_fail_rate:'로그인 실패율', cwd_fail_spike:'CWD 실패 급증'};

function fmtPct(v) { return v == null ? '-' : (v*100).toFixed(1) + '%'; }
function fmtBytesPerSec(v) { return v == null ? '-' : (v / 1024 / 1024).toFixed(2) + ' MB/s'; }
function fmtTime(s) {
  if (!s) return '-';
  // timezone 없는 문자열은 브라우저가 로컬시간으로 파싱하므로 Z 추가해 UTC 강제
  return fmtLocalDateTime(new Date(/[Z+]/.test(s) ? s : s + 'Z'));
}

// 시간대별 차트 x축 라벨: 하루 이내면 'HH시', 그 이상이면 'MM/DD HH시' (UTC 버킷 기준)
function _fmtHourBucket(b, withDate) {
  const d = new Date(b);
  const hh = pad2(d.getUTCHours());
  return withDate ? `${pad2(d.getUTCMonth() + 1)}/${pad2(d.getUTCDate())} ${hh}시` : hh + '시';
}

// 버킷 라벨 — 일 단위면 MM/DD, 시간 단위면 기존 규칙.
// (버킷 문자열은 서버가 로컬 시각을 'Z' 로 붙여 보내므로 getUTC* 로 읽는다)
function _fmtBucketLabel(b, unit, withDate) {
  if (unit !== 'day') return _fmtHourBucket(b, withDate);
  const d = new Date(b);
  return `${pad2(d.getUTCMonth() + 1)}/${pad2(d.getUTCDate())}`;
}
function fmtMetricVal(metric, v) { return metric === 'throughput' ? fmtBytesPerSec(v) : fmtPct(v); }

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

// 시계열 차트 공통 규칙 — 업로드/삭제 카운트 · 업로드양 · 삭제량이 모두 같다.
// (색·굵기·점 크기, x축 눈금 밀도가 차트마다 갈라지면 같은 화면에서 다르게 보인다)
// 주/월(일 단위 합산)도 같은 선 차트로 그린다 — 버킷만 하루 단위로 묶인다.
function _hourlyLineStyle(idx, bucketCount) {
  const color = HOURLY_PALETTE[idx % HOURLY_PALETTE.length];
  return {
    borderColor: color,
    backgroundColor: color + '22',
    borderWidth: 1.5,
    tension: 0,
    pointRadius: bucketCount > 48 ? 0 : 2,
    fill: false,
  };
}

function _hourlyXScale(bucketCount) {
  return {ticks: {font: {size: 10}, maxRotation: 45, autoSkip: true,
                  maxTicksLimit: Math.min(14, Math.max(6, Math.ceil(bucketCount / 24)))}};
}

// 카드 제목 옆 조회기간 표시 — 여러 카드가 같은 문구를 쓴다.
function _setPeriodLabels(...ids) {
  const label = _dashPeriodLabel();
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = label;
  });
}

let _dashExactStart = null;
let _dashExactEnd   = null;

function _dashDateParams() {
  if (_dashExactStart && _dashExactEnd) {
    return `start_date=${encodeURIComponent(_dashExactStart)}&end_date=${encodeURIComponent(_dashExactEnd)}`;
  }
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s && e) {
    return `start_date=${encodeURIComponent(new Date(s + 'T00:00:00').toISOString())}&end_date=${encodeURIComponent(new Date(e + 'T23:59:59').toISOString())}`;
  }
  return 'days=7';
}

// 조회 기간(일). 주/월 처럼 긴 기간은 시간 버킷이면 점이 너무 촘촘해 읽히지 않는다.
function _dashRangeDays() {
  if (_dashExactStart && _dashExactEnd) {
    return (new Date(_dashExactEnd) - new Date(_dashExactStart)) / 86400000;
  }
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s && e) return (new Date(e + 'T23:59:59') - new Date(s + 'T00:00:00')) / 86400000;
  return 7;
}

// 2일을 넘으면 일 단위 합산(막대), 그 이하는 시간 단위(선).
function _dashBucketUnit() { return _dashRangeDays() > 2 ? 'day' : 'hour'; }

function dashLast24() {
  const end   = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  _dashExactStart = start.toISOString();
  _dashExactEnd   = end.toISOString();
  document.getElementById('dashStart').value = fmtLocalDate(start);
  document.getElementById('dashEnd').value   = fmtLocalDate(end);
  document.querySelectorAll('.dash-quick').forEach(b => {
    const active = b.dataset.h24 === 'true';
    b.classList.toggle('btn-primary', active);
    b.classList.toggle('btn-outline-secondary', !active);
  });
  loadAll();
}

function dashQuick(days) {
  _dashExactStart = null;
  _dashExactEnd   = null;
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days + 1);
  document.getElementById('dashStart').value = fmtLocalDate(start);
  document.getElementById('dashEnd').value   = fmtLocalDate(end);
  document.querySelectorAll('.dash-quick').forEach(b => {
    const active = parseInt(b.dataset.days) === days;
    b.classList.toggle('btn-primary', active);
    b.classList.toggle('btn-outline-secondary', !active);
  });
  loadAll();
}

function dashCustom() {
  _dashExactStart = null;
  _dashExactEnd   = null;
  document.querySelectorAll('.dash-quick').forEach(b => {
    b.classList.remove('btn-primary');
    b.classList.add('btn-outline-secondary');
  });
  loadAll();
}

function loadAll() {
  loadServiceHealth();
  loadUserHourly();
}

// ── 대시보드 카드 순서 (드래그) ──────────────────────────────────────────────
// 손잡이(.dash-grip)를 눌렀을 때만 드래그가 시작된다 — 카드 전체를 draggable 로 두면
// 차트의 드래그 확대가 먹지 않는다. 순서는 브라우저(localStorage)에만 저장한다.
const DASH_ORDER_KEY = 'dashCardOrder';

function _dashSlots() {
  return Array.from(document.querySelectorAll('#dashGrid .dash-slot'));
}

function _saveDashOrder() {
  try {
    localStorage.setItem(DASH_ORDER_KEY,
      JSON.stringify(_dashSlots().map(el => el.dataset.card)));
  } catch { /* 저장 실패는 무시 — 순서만 못 기억할 뿐 화면은 정상 */ }
}

function _restoreDashOrder() {
  const grid = document.getElementById('dashGrid');
  if (!grid) return;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(DASH_ORDER_KEY) || 'null'); } catch { saved = null; }
  if (!Array.isArray(saved)) return;
  const byId = Object.fromEntries(_dashSlots().map(el => [el.dataset.card, el]));
  // 저장된 순서에 없는 카드(새로 추가된 것)는 원래 자리에 그대로 남는다
  saved.forEach(id => { if (byId[id]) grid.appendChild(byId[id]); });
}

let _dashDragEl = null;

function initDashLayout() {
  const grid = document.getElementById('dashGrid');
  if (!grid || grid.dataset.dndReady) return;
  grid.dataset.dndReady = '1';
  _restoreDashOrder();

  _dashSlots().forEach(slot => {
    const grip = slot.querySelector('.dash-grip');
    if (grip) {
      grip.addEventListener('mousedown', () => { slot.draggable = true; });
      grip.addEventListener('mouseup',   () => { slot.draggable = false; });
    }
    slot.addEventListener('dragstart', e => {
      _dashDragEl = slot;
      slot.classList.add('dash-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', slot.dataset.card);
    });
    slot.addEventListener('dragend', () => {
      slot.classList.remove('dash-dragging');
      slot.draggable = false;
      _dashDragEl = null;
      _saveDashOrder();
    });
    slot.addEventListener('dragover', e => {
      if (!_dashDragEl || _dashDragEl === slot) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      // 커서가 대상 카드의 앞쪽 절반이면 앞에, 뒤쪽 절반이면 뒤에 넣는다
      const box = slot.getBoundingClientRect();
      const before = (e.clientX - box.left) < box.width / 2;
      grid.insertBefore(_dashDragEl, before ? slot : slot.nextSibling);
    });
    slot.addEventListener('drop', e => e.preventDefault());
  });
}

function resetDashLayout() {
  try { localStorage.removeItem(DASH_ORDER_KEY); } catch { /* noop */ }
  location.reload();
}

// 슬라이스 인덱스 0=전송 실패, 1=로그인 실패, 2=CWD 실패
// CWD 는 건수보다 '어느 경로에 몰렸는지'가 판단 근거라 로그 대신 원인 분석 모달을 연다.
const RATE_DRILL_ACTIONS = [
  () => navToLogsFilters({action: '__transfer_only__', status: 'fail'}),
  () => navToLogsFilters({action: 'login', status: 'fail'}),
  () => openCwdAnalysis(),
];

// 대시보드 날짜 범위를 유지하면서 로그 조회 페이지로 드릴다운
function navToLogsFilters({action = '', status = '', filePath = ''} = {}) {
  if (_dashExactStart && _dashExactEnd) {
    document.getElementById('logStartTime').value = fmtLocalInput(new Date(_dashExactStart));
    document.getElementById('logEndTime').value   = fmtLocalInput(new Date(_dashExactEnd));
  } else {
    const s = document.getElementById('dashStart').value;
    const e = document.getElementById('dashEnd').value;
    if (s) document.getElementById('logStartTime').value = s + 'T00:00';
    if (e) document.getElementById('logEndTime').value   = e + 'T23:59';
  }
  document.getElementById('logActionFilter').value = action;
  document.getElementById('logStatusFilter').value  = status;
  const fileEl = document.getElementById('logFileFilter');
  if (fileEl) fileEl.value = filePath;  // 지정 없으면 이전 검색값이 남지 않게 비운다
  _pendingSearch = true;  // initLogsPage 완료 후 자동 검색 (logs.js)
  nav('logs');
}

// 사용자별 '건수' 추이 카드 — 업로드 카운트·삭제 카운트가 같은 코드를 쓴다.
// (같은 규칙이 두 벌이 되면 한쪽만 고쳐져 두 카드가 갈라진다)
const USER_COUNT_CHARTS = {
  userUploadCnt: {
    canvas: 'chartUserUploadCnt', legend: 'userUploadCntLegend',
    resetBtn: 'resetUserUploadCntZoomBtn', head: '사용자',
    pick: h => h.uploads || 0, empty: '기간 내 업로드 없음',
  },
  userDeleteCnt: {
    canvas: 'chartUserDeleteCnt', legend: 'userDeleteCntLegend',
    resetBtn: 'resetUserDeleteCntZoomBtn', head: '사용자',
    pick: h => h.deletes || 0, empty: '기간 내 삭제 없음',
  },
};

// key → {focus, sort}. 카드마다 따로 둔다(한 카드에서 계정을 골라도 다른 카드는 그대로).
const _userChartState = {};

function _chartState(key) {
  if (!_userChartState[key]) _userChartState[key] = {focus: null, sort: {col: null, asc: true}};
  return _userChartState[key];
}

async function loadUserHourly() {
  _setPeriodLabels('userUploadCntPeriod', 'userDeleteCntPeriod',
                   'userUploadPeriod', 'userDeletePeriod');
  const unit = _dashBucketUnit();
  const data = await api('GET', `/dashboard/users-hourly?${_dashDateParams()}&bucket=${unit}`);
  if (!data) return;

  if (!data.length) {
    Object.keys(USER_COUNT_CHARTS).forEach(k => _renderUserCountChart(k, [], [], String));
    _renderUserVolumeChart('userUpload', 'chartUserUpload', [], [], String, () => [0, 0]);
    _renderUserVolumeChart('userDelete', 'chartUserDelete', [], [], String, () => [0, 0]);
    return;
  }

  // 버킷은 전체 사용자 기준 — 삭제만 한 사용자도 x축에 들어와야 한다
  const bucketSet = new Set(data.flatMap(u => u.data.map(h => h.bucket)));
  const allBuckets = [...bucketSet].sort();
  const fmtBucket = b => _fmtBucketLabel(b, unit, allBuckets.length > 25);

  // 버킷 → 시점 맵. 네 차트(업로드·삭제 카운트, 업로드양·삭제량)가 같은 맵을 쓴다.
  data.forEach(u => { u._map = Object.fromEntries(u.data.map(h => [h.bucket, h])); });

  Object.keys(USER_COUNT_CHARTS).forEach(k => _renderUserCountChart(k, data, allBuckets, fmtBucket));

  // 업로드양 · 삭제량 (기간별) — 같은 응답으로 그린다 (추가 요청 없음).
  _renderUserVolumeChart('userUpload', 'chartUserUpload', data, allBuckets, fmtBucket,
                         h => [h.bytes_in || 0, h.uploads || 0]);
  _renderUserVolumeChart('userDelete', 'chartUserDelete', data, allBuckets, fmtBucket,
                         h => [h.bytes_del || 0, h.deletes || 0]);
}

// 사용자별 건수 라인차트 + 범례표(사용자·최대·현재, 클릭 시 해당 계정만 표시).
function _renderUserCountChart(key, series, buckets, fmtBucket) {
  const cfg = USER_COUNT_CHARTS[key];
  const legendEl = document.getElementById(cfg.legend);
  const st = _chartState(key);
  st.focus = null;
  destroyChart(key);
  document.getElementById(cfg.resetBtn)?.classList.add('d-none');

  const active = series.filter(u => u.data.some(h => cfg.pick(h) > 0));
  if (!active.length) {
    if (legendEl) legendEl.innerHTML = `<div class="text-muted small">${cfg.empty}</div>`;
    return;
  }

  const datasets = active.map((u, i) => ({
    label: u.username,
    data: buckets.map(b => cfg.pick(u._map[b] || {})),
    ..._hourlyLineStyle(i, buckets.length),
  }));

  charts[key] = new Chart(document.getElementById(cfg.canvas), {
    type: 'line',
    data: {labels: buckets.map(fmtBucket), datasets},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {display: false},
        tooltip: {callbacks: {label: c => `${c.dataset.label}: ${c.parsed.y.toLocaleString()}건`}},
        zoom: {
          zoom: {
            drag: {enabled: true, backgroundColor: 'rgba(13,110,253,0.08)', borderColor: 'rgba(13,110,253,0.4)', borderWidth: 1},
            mode: 'x',
            onZoomComplete: () => document.getElementById(cfg.resetBtn)?.classList.remove('d-none'),
          },
        },
      },
      scales: {
        x: _hourlyXScale(buckets.length),
        y: {beginAtZero: true, ticks: {callback: v => v.toLocaleString(), font: {size: 10}}},
      },
    },
  });

  const rows = active.map((u, i) => {
    const vals = datasets[i].data;
    const maxVal = Math.max(0, ...vals);
    const curVal = vals[vals.length - 1] ?? 0;
    const color = HOURLY_PALETTE[i % HOURLY_PALETTE.length];
    return `<tr onclick="focusUserChart('${key}', ${i})" id="${key}LegendItem${i}" style="cursor:pointer" data-name="${esc(u.username)}" data-max="${maxVal}" data-cur="${curVal}">
      <td style="padding:3px 4px;min-width:0;max-width:0">
        <div class="d-flex align-items-center gap-1" style="min-width:0">
          <span style="display:inline-block;width:14px;height:3px;background:${color};border-radius:1px;flex-shrink:0"></span>
          <span class="text-truncate" style="font-size:0.75rem" title="${esc(u.username)}">${esc(u.username)}</span>
        </div>
      </td>
      <td style="text-align:right;padding:3px 4px;white-space:nowrap;font-size:0.75rem">${maxVal.toLocaleString()}</td>
      <td style="text-align:right;padding:3px 4px;white-space:nowrap;font-size:0.75rem">${curVal.toLocaleString()}</td>
    </tr>`;
  }).join('');
  st.sort = {col: null, asc: true};
  legendEl.innerHTML = `<table style="width:100%;border-collapse:collapse;table-layout:fixed">
    <colgroup><col><col style="width:46px"><col style="width:46px"></colgroup>
    <thead><tr style="color:var(--st-muted);border-bottom:1px solid var(--st-border)">
      <th data-col="name" onclick="sortUserChart('${key}', 'name')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:left;cursor:pointer;user-select:none">${cfg.head}<span class="sort-arrow"></span></th>
      <th data-col="max" onclick="sortUserChart('${key}', 'max')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:right;cursor:pointer;user-select:none">최대<span class="sort-arrow"></span></th>
      <th data-col="cur" onclick="sortUserChart('${key}', 'cur')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:right;cursor:pointer;user-select:none">현재<span class="sort-arrow"></span></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// 사용자별 '양' 추이 라인차트 — 업로드양·삭제량 카드가 같은 코드를 쓴다.
// pick(h) → [바이트, 건수]. 값이 모두 0인 사용자는 빼고, 아무도 없으면 안내 문구로 대체한다.
function _renderUserVolumeChart(chartKey, canvasId, series, buckets, fmtBucket, pick) {
  destroyChart(chartKey);
  _chartState(chartKey).focus = null;
  const canvas = document.getElementById(canvasId);
  const empty  = document.getElementById(canvasId + 'Empty');
  if (!canvas || !empty) return;

  const datasets = [];
  series.forEach((u, i) => {
    const picked = buckets.map(b => pick(u._map[b] || {}));
    if (!picked.some(([v]) => v > 0)) return;
    datasets.push({
      label: u.username,
      data: picked.map(([v]) => v),
      counts: picked.map(([, n]) => n),
      ..._hourlyLineStyle(datasets.length, buckets.length),
    });
  });

  if (!datasets.length) {
    canvas.classList.add('d-none');
    empty.classList.remove('d-none');
    return;
  }
  canvas.classList.remove('d-none');
  empty.classList.add('d-none');

  charts[chartKey] = new Chart(canvas, {
    type: 'line',
    data: {labels: buckets.map(fmtBucket), datasets},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {
          position: 'right',
          labels: {boxWidth: 10, font: {size: 10}},
          // 계정을 클릭하면 그 계정만 표시, 같은 계정을 다시 누르면 전체 복원
          // (카운트 카드의 범례 클릭과 같은 동작을 쓴다)
          onClick: (e, item) => focusUserChart(chartKey, item.datasetIndex),
        },
        tooltip: {callbacks: {label: c => {
          const n = c.dataset.counts?.[c.dataIndex] || 0;
          return `${c.dataset.label}: ${fmtBytes(c.parsed.y)} (${n.toLocaleString()}건)`;
        }}},
      },
      scales: {
        x: _hourlyXScale(buckets.length),
        y: {beginAtZero: true, ticks: {callback: v => fmtBytes(v), font: {size: 10}}},
      },
    },
  });
}

function _applyLegendSort(legendId, state) {
  const legendEl = document.getElementById(legendId);
  const tbody = legendEl?.querySelector('tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {
    if (state.col === 'name') {
      const cmp = (a.dataset.name || '').localeCompare(b.dataset.name || '');
      return state.asc ? cmp : -cmp;
    }
    const av = parseFloat(a.dataset[state.col]) || 0;
    const bv = parseFloat(b.dataset[state.col]) || 0;
    return state.asc ? av - bv : bv - av;
  });
  rows.forEach(r => tbody.appendChild(r));
  legendEl.querySelectorAll('thead th').forEach(th => {
    const arrow = th.querySelector('.sort-arrow');
    if (!arrow) return;
    arrow.textContent = th.dataset.col === state.col ? (state.asc ? ' ▲' : ' ▼') : '';
  });
}

function sortUserChart(key, col) {
  const st = _chartState(key);
  if (st.sort.col === col) st.sort.asc = !st.sort.asc;
  else st.sort = {col, asc: col === 'name'};
  _applyLegendSort(USER_COUNT_CHARTS[key].legend, st.sort);
}

function resetUserChartZoom(key) {
  charts[key]?.resetZoom();
  document.getElementById(USER_COUNT_CHARTS[key].resetBtn)?.classList.add('d-none');
}

// 범례에서 계정을 클릭하면 그 계정만 표시, 같은 계정을 다시 클릭하면 전체 복원.
function focusUserChart(key, idx) {
  const chart = charts[key];
  if (!chart) return;
  const st = _chartState(key);
  const clear = st.focus === idx;
  st.focus = clear ? null : idx;
  for (let i = 0; i < chart.data.datasets.length; i++) {
    const show = clear || i === idx;
    chart.setDatasetVisibility(i, show);
    const el = document.getElementById(`${key}LegendItem${i}`);
    if (el) el.style.opacity = show ? '1' : '0.3';
  }
  chart.update();
}

const HOURLY_PALETTE = ['#0d6efd','#198754','#dc3545','#fd7e14','#6f42c1','#20c997','#0dcaf0','#ffc107','#e83e8c','#6c757d'];

function _dashPeriodLabel() {
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s && e) return `${s} ~ ${e}`;
  return '';
}

async function loadServiceHealth() {
  _setPeriodLabels('healthStatusPeriod', 'healthRatePeriod');
  const data = await api('GET', `/dashboard/service-health?${_dashDateParams()}`);
  if (!data) return;

  // 서비스 영향도 도넛
  destroyChart('healthStatus');
  const counts = {ok: 0, warning: 0, critical: 0};
  data.devices.forEach(d => { if (counts[d.status] != null) counts[d.status]++; });
  const totalDevices = counts.ok + counts.warning + counts.critical;
  charts.healthStatus = new Chart(document.getElementById('chartHealthStatus'), {
    type: 'doughnut',
    data: {
      labels: ['정상', '주의', '심각'],
      datasets: [{
        data: [counts.ok, counts.warning, counts.critical],
        backgroundColor: ['#198754', '#ffc107', '#dc3545'],
        hoverOffset: 12, borderWidth: 1,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: {position: 'right', labels: {boxWidth: 12, font: {size: 11}}},
        tooltip: {callbacks: {label: c => `${c.label}: ${c.parsed}대`}},
        centerText: {line1: `${totalDevices}대`, line2: '전체 장비', size: 13},
      },
    },
  });

  // 실패 건수 — 0건이면 이상 없음 표시
  destroyChart('healthRate');
  const ft = data.fail_totals || {};
  // cwd_fails 는 설정의 '제외 경로'를 뺀 값 — 숨긴 건수를 범례/툴팁에 같이 밝힌다
  const cwdIgnored = ft.cwd_fails_ignored || 0;
  const failTotal = (ft.transfer_fails || 0) + (ft.login_fails || 0) + (ft.cwd_fails || 0);
  const rateEl = document.getElementById('chartHealthRate');
  const rateWrap = rateEl.parentElement;

  if (failTotal === 0) {
    rateEl.style.display = 'none';
    if (!rateWrap.querySelector('.no-fail')) {
      const d = document.createElement('div');
      d.className = 'no-fail d-flex flex-column align-items-center justify-content-center h-100 text-success gap-2';
      d.innerHTML = '<i class="bi bi-check-circle-fill" style="font-size:2.8rem"></i>'
        + '<span class="fw-semibold">이상 없음</span>'
        + '<span class="small text-muted">전송 · 로그인 · CWD 실패 없음</span>';
      rateWrap.appendChild(d);
    }
  } else {
    rateEl.style.display = '';
    rateWrap.querySelector('.no-fail')?.remove();
    charts.healthRate = new Chart(rateEl, {
      type: 'doughnut',
      data: {
        labels: ['전송 실패', '로그인 실패', 'CWD 실패 (디렉토리 이동)'],
        datasets: [{
          data: [ft.transfer_fails || 0, ft.login_fails || 0, ft.cwd_fails || 0],
          backgroundColor: ['#dc3545', '#fd7e14', '#6f42c1'],
          hoverOffset: 12, borderWidth: 1,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        onClick: (evt, elems) => {
          if (!elems.length) return;
          RATE_DRILL_ACTIONS[elems[0].index]?.();
        },
        onHover: (evt, elems) => {
          evt.native.target.style.cursor = elems.length ? 'pointer' : 'default';
        },
        plugins: {
          legend: {
            position: 'right',
            labels: {
              boxWidth: 12, font: {size: 11},
              generateLabels: chart => {
                const ds = chart.data.datasets[0];
                return chart.data.labels.map((label, i) => ({
                  text: `${label}: ${ds.data[i].toLocaleString()}건`
                        + (i === 2 && cwdIgnored ? ` (제외 ${cwdIgnored.toLocaleString()}건)` : ''),
                  fillStyle: ds.backgroundColor[i],
                  strokeStyle: ds.backgroundColor[i],
                  hidden: false, index: i,
                }));
              },
            },
          },
          tooltip: {callbacks: {label: c => c.dataIndex === 2
            ? `${c.label}: ${c.parsed.toLocaleString()}건 — 클릭하여 원인 경로 분석`
              + (cwdIgnored ? ` (제외 경로 ${cwdIgnored.toLocaleString()}건 제외됨)` : '')
            : `${c.label}: ${c.parsed.toLocaleString()}건 — 클릭하여 조회`}},
          centerText: {line1: `${failTotal.toLocaleString()}건`, line2: '총 실패', size: 13, color: '#dc3545'},
        },
      },
    });
  }

  const tb = document.getElementById('healthAlerts');
  if (!data.alerts.length) {
    tb.innerHTML = '<tr><td colspan="7" class="text-muted small">최근 알림이 없습니다.</td></tr>';
  } else {
    tb.innerHTML = data.alerts.map(a => {
      const sev = a.severity === 'critical'
        ? '<span class="badge bg-danger">심각</span>'
        : '<span class="badge bg-warning text-dark">주의</span>';
      return `<tr>
        <td class="small">${fmtTime(a.bucket)}</td>
        <td class="small">${a.hostname}</td>
        <td class="small">${METRIC_LABEL[a.metric] || a.metric}</td>
        <td>${sev}</td>
        <td class="small fw-semibold">${fmtMetricVal(a.metric, a.value)}</td>
        <td class="small text-muted">${a.baseline==null?'-':fmtMetricVal(a.metric, a.baseline)}</td>
        <td class="small text-muted">${a.message || ''}</td>
      </tr>`;
    }).join('');
  }
}

// 자동 새로고침 (60초)
let _dashTimer = null;
function toggleDashAutoRefresh() {
  const btn = document.getElementById('btnDashAuto');
  if (_dashTimer) {
    clearInterval(_dashTimer);
    _dashTimer = null;
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-outline-secondary');
    btn.title = '자동 새로고침 켜기';
    btn.querySelector('span').textContent = '자동';
  } else {
    _dashTimer = setInterval(() => { loadAll(); }, 60000);
    btn.classList.add('btn-primary');
    btn.classList.remove('btn-outline-secondary');
    btn.title = '자동 새로고침 끄기 (60초)';
    btn.querySelector('span').textContent = '자동 ON';
  }
}

// ── CWD 실패 원인 분석 ───────────────────────────────────────────────────────
// 건수만으로는 정상 탐색과 설정 오류를 구분할 수 없어, 실패가 몰린 경로를 보여주고
// 원인이 확인된 경로를 그 자리에서 제외(설정 반영)할 수 있게 한다.
let _cwdData = null;

async function openCwdAnalysis() {
  document.getElementById('cwdPeriod').textContent = _dashPeriodLabel();
  document.getElementById('cwdMsg').className = 'alert d-none py-2 small mt-2 mb-0';
  document.getElementById('cwdSummary').innerHTML =
    '<span class="spinner-border spinner-border-sm me-1" style="width:.8rem;height:.8rem"></span>집계 중…';
  document.getElementById('cwdPaths').innerHTML = '<tr><td colspan="7" class="text-muted small">-</td></tr>';
  document.getElementById('cwdUsers').innerHTML = '<tr><td colspan="3" class="text-muted small">-</td></tr>';
  new bootstrap.Modal(document.getElementById('cwdAnalysisModal')).show();
  await loadCwdAnalysis();
}

async function loadCwdAnalysis() {
  const data = await api('GET', `/dashboard/cwd-fails?${_dashDateParams()}`);
  if (!data) return;
  _cwdData = data;
  const isAdmin = getRole() === 'admin';

  // 진짜 이동 실패 = 전체 - (제외 경로 + 존재 확인)
  const counted = data.total - data.ignored - data.probes;
  const share = counted ? Math.round(data.top_share * 100) : 0;
  const hint = !counted
    ? '<div class="alert alert-success py-2 px-3 mt-2 mb-0">이 기간에 <b>실제 디렉토리 이동 실패는 없습니다.</b>'
      + (data.probes ? ' CWD 550 은 모두 업로드 전 경로 존재를 떠본 것으로, 곧이어 그 경로가 만들어졌습니다.' : '')
      + '</div>'
    : share >= 90
      ? '<div class="alert alert-warning py-2 px-3 mt-2 mb-0">상위 3개 경로에 ' + share + '% 가 몰려 있습니다. '
        + '침입 탐색보다 <b>홈·업로드 경로 설정 오류</b>일 가능성이 큽니다 (경로 오타, 마운트 누락, 권한).</div>'
      : '<div class="alert alert-secondary py-2 px-3 mt-2 mb-0">실패가 여러 경로(' + data.distinct_paths.toLocaleString()
        + '개)에 흩어져 있습니다. 사용자·IP 가 여럿이면 탐색성 접근일 수 있으니 오른쪽 표를 확인하세요.</div>';

  document.getElementById('cwdSummary').innerHTML =
    `실제 이동 실패 <b>${counted.toLocaleString()}건</b>`
    + ` · 경로 ${data.distinct_paths.toLocaleString()}개`
    + `<span class="text-muted"> — 원본 ${data.total.toLocaleString()}건 중 `
    + `존재 확인 ${data.probes.toLocaleString()}건`
    + (data.ignored ? `, 제외 경로 ${data.ignored.toLocaleString()}건` : '')
    + ` 제외</span>`
    + hint;

  const paths = data.paths || [];
  document.getElementById('cwdPaths').innerHTML = !paths.length
    ? '<tr><td colspan="7" class="text-muted small">실제 이동 실패가 없습니다.</td></tr>'
    : paths.map((p, i) => {
        const pct = data.total ? (p.count / data.total * 100).toFixed(1) : '0.0';
        const path = p.file_path || '(경로 없음)';
        const btn = !isAdmin ? ''
          : p.ignored
            ? `<button class="btn btn-outline-secondary btn-xs" onclick="cwdIgnoreToggle(${i}, false)">제외 해제</button>`
            : `<button class="btn btn-outline-warning btn-xs" onclick="cwdIgnoreToggle(${i}, true)">제외</button>`;
        return `<tr${p.ignored ? ' class="text-muted"' : ''}>
          <td class="small text-break">${esc(path)}${p.ignored ? ' <span class="badge bg-secondary">제외됨</span>' : ''}</td>
          <td class="small text-end fw-semibold">${p.count.toLocaleString()}</td>
          <td class="small text-end">${pct}%</td>
          <td class="small text-end">${p.users.toLocaleString()}</td>
          <td class="small text-end">${p.ips.toLocaleString()}</td>
          <td class="small text-muted">${fmtTime(p.last_seen)}</td>
          <td class="text-end text-nowrap">
            <button class="btn btn-outline-primary btn-xs me-1" onclick="cwdViewLogs(${i})">로그</button>${btn}
          </td>
        </tr>`;
      }).join('');

  const users = data.users || [];
  document.getElementById('cwdUsers').innerHTML = !users.length
    ? '<tr><td colspan="3" class="text-muted small">실제 이동 실패가 없습니다.</td></tr>'
    : users.map(u => `<tr>
        <td class="small">${esc(u.username || '(미상)')}</td>
        <td class="small text-end fw-semibold">${u.count.toLocaleString()}</td>
        <td class="small text-end">${u.paths.toLocaleString()}</td>
      </tr>`).join('');
}

// 해당 경로의 CWD 실패만 로그 조회로 드릴다운 (대시보드 기간 유지)
function cwdViewLogs(idx) {
  const p = _cwdData?.paths?.[idx];
  if (!p) return;
  bootstrap.Modal.getInstance(document.getElementById('cwdAnalysisModal'))?.hide();
  navToLogsFilters({action: 'cwd_fail', status: '', filePath: p.file_path || ''});
}

// 경로를 설정의 'CWD 실패 제외 경로'에 넣거나 뺀다 (관리자 전용)
async function cwdIgnoreToggle(idx, add) {
  const p = _cwdData?.paths?.[idx];
  if (!p || !p.file_path) return;
  const list = (_cwdData.ignore_patterns || []).filter(x => x !== p.file_path);
  if (add) list.push(p.file_path);
  const msg = document.getElementById('cwdMsg');
  try {
    await api('PUT', '/settings/alerts', {cwd_ignore_paths: list.join('\n')});
    msg.className = 'alert alert-success py-2 small mt-2 mb-0';
    msg.textContent = add
      ? `${p.file_path} 을(를) 제외했습니다. 실패 건수·추이·알림에서 빠집니다 (다음 판정 주기부터 알림 반영).`
      : `${p.file_path} 제외를 해제했습니다.`;
    await loadCwdAnalysis();
    loadServiceHealth();
  } catch (e) {
    msg.className = 'alert alert-danger py-2 small mt-2 mb-0';
    msg.textContent = e.message;
  }
}

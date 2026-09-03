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
    ctx.fillStyle = ct.color || '#333';
    ctx.fillText(ct.line1 || '', cx, ct.line2 ? cy - 9 : cy);
    if (ct.line2) {
      ctx.font = `${(ct.size || 13) - 1}px sans-serif`;
      ctx.fillStyle = ct.subColor || '#888';
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
function fmtMetricVal(metric, v) { return metric === 'throughput' ? fmtBytesPerSec(v) : fmtPct(v); }

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
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
  loadDashboard();
  loadServiceHealth();
  loadUserHourly();
  loadHourly();
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

async function loadDashboard() {
  const data = await api('GET', `/dashboard?${_dashDateParams()}`);
  if (!data) return;

  const bytesBarOpts = {
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {display: false},
      tooltip: {callbacks: {label: c => fmtBytes(c.parsed.x)}},
    },
    scales: {x: {beginAtZero: true, ticks: {callback: v => fmtBytes(v), font: {size: 10}}}},
  };

}

let _userHourlyFocusIdx = null;

async function loadUserHourly() {
  _userHourlyFocusIdx = null;
  const data = await api('GET', `/dashboard/users-hourly?${_dashDateParams()}`);
  if (!data) return;

  const legendEl = document.getElementById('userHourlyLegend');
  if (!data.length) {
    destroyChart('userHourly');
    legendEl.innerHTML = '<div class="text-muted small">사용자 데이터 없음</div>';
    return;
  }

  const active = data.filter(u => u.data.some(h => (h.uploads || 0) + (h.downloads || 0) > 0));
  if (!active.length) {
    destroyChart('userHourly');
    legendEl.innerHTML = '<div class="text-muted small">사용자 데이터 없음</div>';
    return;
  }
  const bucketSet = new Set(active.flatMap(u => u.data.map(h => h.bucket)));
  const allBuckets = [...bucketSet].sort();

  const fmtBucket = b => _fmtHourBucket(b, allBuckets.length > 25);

  const datasets = active.map((u, i) => {
    const map = Object.fromEntries(u.data.map(h => [h.bucket, h]));
    return {
      label: u.username,
      data: allBuckets.map(b => (map[b]?.uploads || 0) + (map[b]?.downloads || 0)),
      borderColor: HOURLY_PALETTE[i % HOURLY_PALETTE.length],
      backgroundColor: HOURLY_PALETTE[i % HOURLY_PALETTE.length] + '22',
      borderWidth: 1.5,
      tension: 0,
      pointRadius: allBuckets.length > 48 ? 0 : 2,
      fill: false,
    };
  });

  document.getElementById('resetUserZoomBtn')?.classList.add('d-none');
  destroyChart('userHourly');
  charts.userHourly = new Chart(document.getElementById('chartUserHourly'), {
    type: 'line',
    data: {labels: allBuckets.map(fmtBucket), datasets},
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
            onZoomComplete: () => document.getElementById('resetUserZoomBtn')?.classList.remove('d-none'),
          },
        },
      },
      scales: {
        x: {ticks: {font: {size: 10}, maxRotation: 45, autoSkip: true, maxTicksLimit: Math.min(14, Math.max(6, Math.ceil(allBuckets.length / 24)))}},
        y: {beginAtZero: true, ticks: {callback: v => v.toLocaleString(), font: {size: 10}}},
      },
    },
  });

  const userRows = active.map((u, i) => {
    const vals = datasets[i].data;
    const maxVal = Math.max(0, ...vals);
    const curVal = vals[vals.length - 1] ?? 0;
    const color = HOURLY_PALETTE[i % HOURLY_PALETTE.length];
    return `<tr onclick="focusUserSeries(${i})" id="userHourlyLegendItem${i}" style="cursor:pointer" data-name="${u.username}" data-max="${maxVal}" data-cur="${curVal}">
      <td style="padding:3px 4px;min-width:0;max-width:0">
        <div class="d-flex align-items-center gap-1" style="min-width:0">
          <span style="display:inline-block;width:14px;height:3px;background:${color};border-radius:1px;flex-shrink:0"></span>
          <span class="text-truncate" style="font-size:0.75rem" title="${u.username}">${u.username}</span>
        </div>
      </td>
      <td style="text-align:right;padding:3px 4px;white-space:nowrap;font-size:0.75rem">${maxVal.toLocaleString()}</td>
      <td style="text-align:right;padding:3px 4px;white-space:nowrap;font-size:0.75rem">${curVal.toLocaleString()}</td>
    </tr>`;
  }).join('');
  _userLegendSort = {col: null, asc: true};
  legendEl.innerHTML = `<table style="width:100%;border-collapse:collapse;table-layout:fixed">
    <colgroup><col><col style="width:46px"><col style="width:46px"></colgroup>
    <thead><tr style="color:#6c757d;border-bottom:1px solid #dee2e6">
      <th data-col="name" onclick="sortUserLegend('name')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:left;cursor:pointer;user-select:none">사용자<span class="sort-arrow"></span></th>
      <th data-col="max" onclick="sortUserLegend('max')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:right;cursor:pointer;user-select:none">최대<span class="sort-arrow"></span></th>
      <th data-col="cur" onclick="sortUserLegend('cur')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:right;cursor:pointer;user-select:none">현재<span class="sort-arrow"></span></th>
    </tr></thead>
    <tbody>${userRows}</tbody>
  </table>`;
}

let _userLegendSort   = {col: null, asc: true};
let _hourlyLegendSort = {col: null, asc: true};

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

function sortUserLegend(col) {
  if (_userLegendSort.col === col) _userLegendSort.asc = !_userLegendSort.asc;
  else _userLegendSort = {col, asc: col === 'name'};
  _applyLegendSort('userHourlyLegend', _userLegendSort);
}

function sortHourlyLegend(col) {
  if (_hourlyLegendSort.col === col) _hourlyLegendSort.asc = !_hourlyLegendSort.asc;
  else _hourlyLegendSort = {col, asc: col === 'name'};
  _applyLegendSort('hourlyGroupLegend', _hourlyLegendSort);
}

function resetUserHourlyZoom() {
  charts.userHourly?.resetZoom();
  document.getElementById('resetUserZoomBtn')?.classList.add('d-none');
}

function focusUserSeries(idx) {
  if (!charts.userHourly) return;
  const total = charts.userHourly.data.datasets.length;
  if (_userHourlyFocusIdx === idx) {
    _userHourlyFocusIdx = null;
    for (let i = 0; i < total; i++) {
      charts.userHourly.setDatasetVisibility(i, true);
      const el = document.getElementById('userHourlyLegendItem' + i);
      if (el) el.style.opacity = '1';
    }
  } else {
    _userHourlyFocusIdx = idx;
    for (let i = 0; i < total; i++) {
      const show = i === idx;
      charts.userHourly.setDatasetVisibility(i, show);
      const el = document.getElementById('userHourlyLegendItem' + i);
      if (el) el.style.opacity = show ? '1' : '0.3';
    }
  }
  charts.userHourly.update();
}

const HOURLY_PALETTE = ['#0d6efd','#198754','#dc3545','#fd7e14','#6f42c1','#20c997','#0dcaf0','#ffc107','#e83e8c','#6c757d'];

async function loadHourly() {
  _hourlyFocusIdx = null;
  const data = await api('GET', `/dashboard/hourly?${_dashDateParams()}`);
  if (!data) return;

  const legendEl = document.getElementById('hourlyGroupLegend');
  if (!data.length) {
    destroyChart('hourly');
    legendEl.innerHTML = '<div class="text-muted small">그룹 데이터 없음</div>';
    return;
  }

  const active = data.filter(g => g.data.some(h => (h.uploads || 0) + (h.downloads || 0) > 0));
  if (!active.length) {
    destroyChart('hourly');
    legendEl.innerHTML = '<div class="text-muted small">그룹 데이터 없음</div>';
    return;
  }
  const bucketSet = new Set(active.flatMap(g => g.data.map(h => h.bucket)));
  const allBuckets = [...bucketSet].sort();

  const fmtBucket = b => _fmtHourBucket(b, allBuckets.length > 25);

  const datasets = active.map((g, i) => {
    const map = Object.fromEntries(g.data.map(h => [h.bucket, h]));
    return {
      label: g.name,
      data: allBuckets.map(b => (map[b]?.uploads || 0) + (map[b]?.downloads || 0)),
      borderColor: HOURLY_PALETTE[i % HOURLY_PALETTE.length],
      backgroundColor: HOURLY_PALETTE[i % HOURLY_PALETTE.length] + '22',
      borderWidth: 1.5,
      tension: 0,
      pointRadius: allBuckets.length > 48 ? 0 : 2,
      fill: false,
    };
  });

  document.getElementById('resetHourlyZoomBtn')?.classList.add('d-none');
  destroyChart('hourly');
  charts.hourly = new Chart(document.getElementById('chartHourly'), {
    type: 'line',
    data: {labels: allBuckets.map(fmtBucket), datasets},
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
            onZoomComplete: () => document.getElementById('resetHourlyZoomBtn')?.classList.remove('d-none'),
          },
        },
      },
      scales: {
        x: {ticks: {font: {size: 10}, maxRotation: 45, autoSkip: true, maxTicksLimit: Math.min(14, Math.max(6, Math.ceil(allBuckets.length / 24)))}},
        y: {beginAtZero: true, ticks: {callback: v => v.toLocaleString(), font: {size: 10}}},
      },
    },
  });

  const groupRows = active.map((g, i) => {
    const vals = datasets[i].data;
    const maxVal = Math.max(0, ...vals);
    const curVal = vals[vals.length - 1] ?? 0;
    const color = HOURLY_PALETTE[i % HOURLY_PALETTE.length];
    return `<tr onclick="focusHourlySeries(${i})" id="hourlyLegendItem${i}" style="cursor:pointer" data-name="${g.name}" data-max="${maxVal}" data-cur="${curVal}">
      <td style="padding:3px 4px;min-width:0;max-width:0">
        <div class="d-flex align-items-center gap-1" style="min-width:0">
          <span style="display:inline-block;width:14px;height:3px;background:${color};border-radius:1px;flex-shrink:0"></span>
          <span class="text-truncate" style="font-size:0.75rem" title="${g.name}">${g.name}</span>
        </div>
      </td>
      <td style="text-align:right;padding:3px 4px;white-space:nowrap;font-size:0.75rem">${maxVal.toLocaleString()}</td>
      <td style="text-align:right;padding:3px 4px;white-space:nowrap;font-size:0.75rem">${curVal.toLocaleString()}</td>
    </tr>`;
  }).join('');
  _hourlyLegendSort = {col: null, asc: true};
  legendEl.innerHTML = `<table style="width:100%;border-collapse:collapse;table-layout:fixed">
    <colgroup><col><col style="width:46px"><col style="width:46px"></colgroup>
    <thead><tr style="color:#6c757d;border-bottom:1px solid #dee2e6">
      <th data-col="name" onclick="sortHourlyLegend('name')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:left;cursor:pointer;user-select:none">그룹<span class="sort-arrow"></span></th>
      <th data-col="max" onclick="sortHourlyLegend('max')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:right;cursor:pointer;user-select:none">최대<span class="sort-arrow"></span></th>
      <th data-col="cur" onclick="sortHourlyLegend('cur')" style="font-size:0.7rem;font-weight:600;padding:2px 4px;text-align:right;cursor:pointer;user-select:none">현재<span class="sort-arrow"></span></th>
    </tr></thead>
    <tbody>${groupRows}</tbody>
  </table>`;
}

function resetHourlyZoom() {
  charts.hourly?.resetZoom();
  document.getElementById('resetHourlyZoomBtn')?.classList.add('d-none');
}

let _hourlyFocusIdx = null;

function focusHourlySeries(idx) {
  if (!charts.hourly) return;
  const total = charts.hourly.data.datasets.length;
  if (_hourlyFocusIdx === idx) {
    // 같은 그룹 재클릭 → 전체 표시 복원
    _hourlyFocusIdx = null;
    for (let i = 0; i < total; i++) {
      charts.hourly.setDatasetVisibility(i, true);
      const el = document.getElementById('hourlyLegendItem' + i);
      if (el) el.style.opacity = '1';
    }
  } else {
    // 선택 그룹만 표시, 나머지 숨김
    _hourlyFocusIdx = idx;
    for (let i = 0; i < total; i++) {
      const show = i === idx;
      charts.hourly.setDatasetVisibility(i, show);
      const el = document.getElementById('hourlyLegendItem' + i);
      if (el) el.style.opacity = show ? '1' : '0.3';
    }
  }
  charts.hourly.update();
}

function _dashPeriodLabel() {
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s && e) return `${s} ~ ${e}`;
  return '';
}

async function loadServiceHealth() {
  const periodLabel = _dashPeriodLabel();
  ['healthStatusPeriod', 'healthRatePeriod'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = periodLabel;
  });
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

  const counted = data.total - data.ignored;
  const share = data.total ? Math.round(data.top_share * 100) : 0;
  const hint = !data.total ? ''
    : share >= 90
      ? '<div class="alert alert-warning py-2 px-3 mt-2 mb-0">상위 3개 경로에 ' + share + '% 가 몰려 있습니다. '
        + '침입 탐색보다 <b>홈·업로드 경로 설정 오류</b>일 가능성이 큽니다 (경로 오타, 마운트 누락, 권한).</div>'
      : '<div class="alert alert-secondary py-2 px-3 mt-2 mb-0">실패가 여러 경로(' + data.distinct_paths.toLocaleString()
        + '개)에 흩어져 있습니다. 사용자·IP 가 소수에 몰려 있다면 탐색성 접근일 수 있으니 오른쪽 표를 확인하세요.</div>';

  document.getElementById('cwdSummary').innerHTML =
    `총 <b>${data.total.toLocaleString()}건</b>`
    + ` · 집계 대상 <b>${counted.toLocaleString()}건</b>`
    + (data.ignored ? ` · 제외 경로 ${data.ignored.toLocaleString()}건` : '')
    + ` · 경로 ${data.distinct_paths.toLocaleString()}개`
    + hint;

  const paths = data.paths || [];
  document.getElementById('cwdPaths').innerHTML = !paths.length
    ? '<tr><td colspan="7" class="text-muted small">기간 내 CWD 실패가 없습니다.</td></tr>'
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
    ? '<tr><td colspan="3" class="text-muted small">집계 대상 실패가 없습니다.</td></tr>'
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

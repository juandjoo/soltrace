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

const HEALTH_STATUS = {
  ok:       {label:'정상', cls:'success', icon:'check-circle'},
  warning:  {label:'주의', cls:'warning', icon:'exclamation-triangle'},
  critical: {label:'심각', cls:'danger',  icon:'exclamation-octagon'},
  idle:     {label:'유휴', cls:'secondary', icon:'dash-circle'},
};
const METRIC_LABEL = {fail_rate:'전송 실패율', throughput:'전송 속도', login_fail_rate:'로그인 실패율', cwd_fail_spike:'CWD 실패 급증'};

function fmtPct(v) { return v == null ? '-' : (v*100).toFixed(1) + '%'; }
function fmtMbps(v) { return v == null ? '-' : v.toFixed(2) + ' MB/s'; }
function fmtTime(s) { return s ? new Date(s).toISOString().slice(0,16).replace('T',' ') : '-'; }
function fmtMetricVal(metric, v) { return metric === 'throughput' ? fmtMbps(v) : fmtPct(v); }

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function _localDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
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
  return 'days=1';
}

function dashLast24() {
  const end   = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  _dashExactStart = start.toISOString();
  _dashExactEnd   = end.toISOString();
  document.getElementById('dashStart').value = _localDateStr(start);
  document.getElementById('dashEnd').value   = _localDateStr(end);
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
  document.getElementById('dashStart').value = _localDateStr(start);
  document.getElementById('dashEnd').value   = _localDateStr(end);
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
const RATE_DRILL_FILTERS = [
  {action: '', status: 'fail'},
  {action: 'login', status: 'fail'},
  {action: 'cwd_fail', status: ''},
];

// 대시보드 날짜 범위를 유지하면서 로그 조회 페이지로 드릴다운
function navToLogsFilters({action = '', status = ''} = {}) {
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s) document.getElementById('logStartTime').value = s + 'T00:00';
  if (e) document.getElementById('logEndTime').value   = e + 'T23:59';
  document.getElementById('logActionFilter').value = action;
  document.getElementById('logStatusFilter').value  = status;
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

  const fmtBucket = b => {
    const d = new Date(b);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    if (allBuckets.length <= 25) return hh + '시';
    const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    return `${mm}/${dd} ${hh}시`;
  };

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

  legendEl.innerHTML = active.map((u, i) => `
    <div class="d-flex align-items-center gap-2 mb-2" style="cursor:pointer"
         onclick="focusUserSeries(${i})" id="userHourlyLegendItem${i}">
      <span style="display:inline-block;width:18px;height:3px;background:${HOURLY_PALETTE[i % HOURLY_PALETTE.length]};border-radius:1px;flex-shrink:0"></span>
      <div class="small text-truncate" style="min-width:0" title="${u.username}">${u.username}</div>
    </div>`).join('');
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

  const fmtBucket = b => {
    const d = new Date(b);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    if (allBuckets.length <= 25) return hh + '시';
    const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    return `${mm}/${dd} ${hh}시`;
  };

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

  legendEl.innerHTML = active.map((g, i) => `
    <div class="d-flex align-items-center gap-2 mb-2" style="cursor:pointer"
         onclick="focusHourlySeries(${i})" id="hourlyLegendItem${i}">
      <span style="display:inline-block;width:18px;height:3px;background:${HOURLY_PALETTE[i % HOURLY_PALETTE.length]};border-radius:1px;flex-shrink:0"></span>
      <div class="small text-truncate" style="min-width:0" title="${g.name}">${g.name}</div>
    </div>`).join('');
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
          navToLogsFilters(RATE_DRILL_FILTERS[elems[0].index]);
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
                  text: `${label}: ${ds.data[i].toLocaleString()}건`,
                  fillStyle: ds.backgroundColor[i],
                  strokeStyle: ds.backgroundColor[i],
                  hidden: false, index: i,
                }));
              },
            },
          },
          tooltip: {callbacks: {label: c => `${c.label}: ${c.parsed.toLocaleString()}건 — 클릭하여 조회`}},
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

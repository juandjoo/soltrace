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

function _dashDateParams() {
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s && e) {
    return `start_date=${encodeURIComponent(new Date(s).toISOString())}&end_date=${encodeURIComponent(new Date(e + 'T23:59:59').toISOString())}`;
  }
  return 'days=7';
}

function dashQuick(days) {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days + 1);
  document.getElementById('dashStart').value = start.toISOString().slice(0, 10);
  document.getElementById('dashEnd').value = end.toISOString().slice(0, 10);
  document.querySelectorAll('.dash-quick').forEach(b => {
    const active = parseInt(b.dataset.days) === days;
    b.classList.toggle('btn-primary', active);
    b.classList.toggle('btn-outline-secondary', !active);
  });
  loadAll();
}

function dashCustom() {
  document.querySelectorAll('.dash-quick').forEach(b => {
    b.classList.remove('btn-primary');
    b.classList.add('btn-outline-secondary');
  });
  loadAll();
}

function loadAll() {
  loadDashboard();
  loadServiceHealth();
}

// 대시보드 날짜 범위를 유지하면서 로그 조회 페이지로 드릴다운
function navToLogsFilters({action = '', status = ''} = {}) {
  const s = document.getElementById('dashStart').value;
  const e = document.getElementById('dashEnd').value;
  if (s) document.getElementById('logStartTime').value = s + 'T00:00';
  if (e) document.getElementById('logEndTime').value   = e + 'T23:59';
  document.getElementById('logActionFilter').value = action;
  document.getElementById('logStatusFilter').value  = status;
  nav('logs');
  setTimeout(() => searchLogs(1), 400);
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
    scales: {x: {beginAtZero: true, ticks: {callback: v => fmtBytes(v)}}},
  };

  const PALETTE = ['#0d6efd','#198754','#fd7e14','#6f42c1','#dc3545','#20c997','#0dcaf0','#ffc107','#e83e8c','#6c757d'];

  destroyChart('topUsers');
  // 내림차순 정렬 (많은 사용자 → 시계방향 첫 슬라이스)
  const tu = (data.top_users || []).slice().sort((a, b) => b.bytes - a.bytes).slice(0, 10);
  const totalUserBytes = tu.reduce((s, u) => s + (u.bytes || 0), 0);
  charts.topUsers = new Chart(document.getElementById('chartTopUsers'), {
    type: 'doughnut',
    data: {labels: tu.map(u => u.label), datasets: [{data: tu.map(u => u.bytes), backgroundColor: PALETTE, hoverOffset: 12, borderWidth: 1}]},
    options: {
      responsive: true, maintainAspectRatio: false,
      rotation: 0,         // Chart.js 4: 0 = 12시 방향 시작
      plugins: {
        legend: {display: false},
        tooltip: {callbacks: {label: c => `${c.label}: ${fmtBytes(c.parsed)}`}},
        centerText: totalUserBytes > 0 ? {line1: fmtBytes(totalUserBytes), line2: '총 사용량', size: 13} : null,
      },
    },
  });
  // 커스텀 범례: 사용자명 + 용량 (많은 순)
  const legendEl = document.getElementById('topUsersLegend');
  legendEl.innerHTML = tu.map((u, i) => `
    <li class="d-flex align-items-center gap-2 mb-1" style="min-width:0">
      <span style="width:10px;height:10px;border-radius:2px;background:${PALETTE[i]};flex-shrink:0"></span>
      <span class="text-truncate" style="flex:1;min-width:0" title="${u.label}">${u.label}</span>
      <span class="text-muted ms-1" style="flex-shrink:0;white-space:nowrap">${fmtBytes(u.bytes)}</span>
    </li>`).join('');

  destroyChart('topGroups');
  const tg = (data.top_groups || []).slice(0, 8);
  const gEl = document.getElementById('chartTopGroups');
  if (!tg.length) {
    destroyChart('topGroups');
    gEl.parentElement.querySelector('.no-group')?.remove();
    const p = document.createElement('div');
    p.className = 'no-group text-muted small';
    p.textContent = '그룹에 속한 장비의 사용량이 없습니다.';
    gEl.after(p);
  } else {
    gEl.parentElement.querySelector('.no-group')?.remove();
    charts.topGroups = new Chart(gEl, {
      type: 'bar',
      data: {labels: tg.map(g => g.label), datasets: [{label: '사용량', data: tg.map(g => g.bytes), backgroundColor: '#6f42c188'}]},
      options: {
        ...bytesBarOpts,
        plugins: {
          ...bytesBarOpts.plugins,
          tooltip: {callbacks: {
            label: c => fmtBytes(c.parsed.x),
            afterLabel: c => { const t = tg[c.dataIndex]?.customer; return t ? `고객사: ${t}` : ''; },
          }},
        },
      },
    });
  }
}

async function loadServiceHealth() {
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
    // 슬라이스 클릭 시 로그 조회로 드릴다운
    // 인덱스 0=전송 실패(status=fail), 1=로그인 실패(action=login,status=fail), 2=CWD 실패(action=cwd_fail)
    const _rateFilters = [
      {action: '', status: 'fail'},
      {action: 'login', status: 'fail'},
      {action: 'cwd_fail', status: ''},
    ];
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
          navToLogsFilters(_rateFilters[elems[0].index]);
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

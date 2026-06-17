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

async function loadDashboard() {
  const days = document.getElementById('dashDays').value;
  const data = await api('GET', `/dashboard?days=${days}`);
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

  const PALETTE = ['#0d6efd','#198754','#fd7e14','#6f42c1','#dc3545','#20c997','#0dcaf0','#ffc107'];
  const donutBytesOpts = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {position: 'right', labels: {boxWidth: 12, font: {size: 11}}},
      tooltip: {callbacks: {label: c => `${c.label}: ${fmtBytes(c.parsed)}`}},
    },
  };

  destroyChart('topUsers');
  const tu = (data.top_users || []).slice(0, 8);
  charts.topUsers = new Chart(document.getElementById('chartTopUsers'), {
    type: 'doughnut',
    data: {labels: tu.map(u => u.label), datasets: [{data: tu.map(u => u.bytes), backgroundColor: PALETTE, hoverOffset: 12, borderWidth: 1}]},
    options: donutBytesOpts,
  });

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
      options: bytesBarOpts,
    });
  }
}

async function loadServiceHealth() {
  const hours = document.getElementById('healthHours').value;
  const data = await api('GET', `/dashboard/service-health?hours=${hours}`);
  if (!data) return;

  destroyChart('healthStatus');
  const counts = {ok: 0, warning: 0, critical: 0};
  data.devices.forEach(d => { if (counts[d.status] != null) counts[d.status]++; });
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
      },
    },
  });

  destroyChart('healthRate');
  const ft = data.fail_totals || {};
  charts.healthRate = new Chart(document.getElementById('chartHealthRate'), {
    type: 'doughnut',
    data: {
      labels: ['전송 실패', '로그인 실패', 'CWD 실패'],
      datasets: [{
        data: [ft.transfer_fails || 0, ft.login_fails || 0, ft.cwd_fails || 0],
        backgroundColor: ['#dc3545', '#fd7e14', '#6f42c1'],
        hoverOffset: 12, borderWidth: 1,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
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
        tooltip: {callbacks: {label: c => `${c.label}: ${c.parsed.toLocaleString()}건`}},
      },
    },
  });

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

function statusBadge(s) {
  const map = {pending:'warning', confirmed:'success', disabled:'secondary'};
  const labels = {pending:'대기중', confirmed:'확인됨', disabled:'비활성'};
  return `<span class="badge bg-${map[s]||'secondary'} text-${s==='pending'?'dark':''}">${labels[s]||s}</span>`;
}

function daemonBadge(s, errMsg) {
  const cfg = {
    running:  {cls:'success',  icon:'bi-check-circle-fill', label:'정상'},
    degraded: {cls:'warning',  icon:'bi-exclamation-triangle-fill', label:'저하'},
    error:    {cls:'danger',   icon:'bi-x-circle-fill', label:'오류'},
    stopping: {cls:'secondary',icon:'bi-stop-circle', label:'종료중'},
    unknown:  {cls:'light',    icon:'bi-question-circle', label:'미확인'},
  };
  const c = cfg[s] || cfg.unknown;
  const tip = errMsg ? ` title="${errMsg.replace(/"/g,'&quot;')}" data-bs-toggle="tooltip"` : '';
  return `<span class="badge bg-${c.cls} text-${s==='unknown'?'dark':''}"${tip}><i class="bi ${c.icon} me-1"></i>${c.label}</span>`;
}

function metricBar(val, max, unit, warnAt, dangerAt) {
  if (val == null) return '<span class="text-muted small">-</span>';
  const pct = Math.min(100, (val / max) * 100);
  const color = val >= dangerAt ? 'danger' : val >= warnAt ? 'warning' : 'success';
  return `<div style="min-width:70px">
    <div class="d-flex justify-content-between" style="font-size:11px">
      <span>${val.toFixed(1)}${unit}</span>
    </div>
    <div class="progress" style="height:4px">
      <div class="progress-bar bg-${color}" style="width:${pct}%"></div>
    </div>
  </div>`;
}

let _deviceCache = [];

async function loadDevices() {
  const filter = document.getElementById('deviceFilter').value;
  const url = '/devices' + (filter ? `?status=${filter}` : '');
  const devices = await api('GET', url);
  if (!devices) return;
  _deviceCache = devices;

  const tbody = document.getElementById('deviceTable');
  if (!devices.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">등록된 장비가 없습니다.</td></tr>';
    return;
  }

  tbody.innerHTML = devices.map(d => {
    const hbDiff = d.last_heartbeat ? (Date.now() - new Date(d.last_heartbeat)) / 1000 : 9999;
    const offline = hbDiff > 120;
    const dStatus = offline && d.daemon_status === 'running' ? 'degraded' : (d.daemon_status || 'unknown');
    const errMsg = d.error_message || (offline ? `마지막 하트비트: ${timeAgo(d.last_heartbeat)}` : null);

    return `<tr class="${dStatus==='error'?'table-danger-subtle':''}">
      <td>
        <strong>${d.hostname}</strong>
        <div class="text-muted" style="font-size:11px">${d.daemon_version||''}</div>
      </td>
      <td class="text-muted small">${d.ip_address||'-'}</td>
      <td>${statusBadge(d.status)}</td>
      <td>
        ${daemonBadge(dStatus, errMsg)}
        <div class="text-muted" style="font-size:11px">${fmtUptime(d.daemon_uptime)} 가동</div>
      </td>
      <td>
        <div class="d-flex gap-2 align-items-center">
          ${metricBar(d.cpu_percent, 100, '%', 70, 90)}
          ${metricBar(d.mem_mb, 512, 'MB', 400, 480)}
          ${d.disk_free_gb != null ? `<span class="small text-muted">💾${d.disk_free_gb.toFixed(1)}G</span>` : ''}
        </div>
      </td>
      <td class="small">
        ${d.buffer_lines > 0 ? `<span class="badge bg-warning text-dark">${d.buffer_lines.toLocaleString()}건</span>` : '<span class="text-muted">-</span>'}
        ${d.queue_size > 0 ? `<span class="badge bg-info text-dark ms-1">Q:${d.queue_size}</span>` : ''}
      </td>
      <td class="text-muted small">${timeAgo(d.last_heartbeat)}</td>
      <td>${d.groups.map(g=>`<span class="badge bg-light text-dark border me-1">${g.name}</span>`).join('')||'<span class="text-muted small">-</span>'}</td>
      <td class="text-end text-nowrap">
        ${d.status === 'pending' ? `<button class="btn btn-xs btn-success me-1" onclick="confirmDevice(${d.id})">확인</button>` : ''}
        ${d.status === 'confirmed' ? `<button class="btn btn-xs btn-warning me-1" onclick="disableDevice(${d.id})">비활성</button>` : ''}
        ${d.status === 'disabled' ? `<button class="btn btn-xs btn-success me-1" onclick="enableDevice(${d.id})">활성화</button>` : ''}
        <button class="btn btn-xs btn-outline-info me-1" onclick="showDeviceStatus(${d.id})"><i class="bi bi-activity"></i></button>
        <button class="btn btn-xs btn-outline-secondary me-1" onclick="openDeviceGroups(${d.id},'${d.hostname}',${JSON.stringify(d.groups.map(g=>g.id))})">그룹</button>
        <button class="btn btn-xs ${d.update_requested ? 'btn-warning' : 'btn-outline-success'} me-1" onclick="requestDaemonUpdate(${d.id})" title="데몬 업데이트"><i class="bi bi-arrow-repeat"></i></button>
        <button class="btn btn-xs btn-outline-danger" onclick="deleteDevice(${d.id})"><i class="bi bi-trash"></i></button>
      </td>
    </tr>`;
  }).join('');

  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el =>
    new bootstrap.Tooltip(el, {placement:'top'})
  );
}

function showDeviceStatus(id) {
  const d = _deviceCache.find(x => x.id === id);
  if (!d) return;
  document.getElementById('dsHostname').textContent = d.hostname;
  const rows = [
    ['데몬 상태',       daemonBadge(d.daemon_status||'unknown', d.error_message)],
    ['에러 메시지',     d.error_message ? `<span class="text-danger">${d.error_message}</span>` : '-'],
    ['마지막 전송',     d.last_send_time ? new Date(d.last_send_time).toLocaleString('ko-KR') : '-'],
    ['연속 실패',       d.consecutive_failures != null ? `${d.consecutive_failures}회` : '-'],
    ['버퍼 대기',       d.buffer_lines != null ? `${d.buffer_lines.toLocaleString()}건` : '-'],
    ['큐 대기',         d.queue_size != null ? `${d.queue_size}건` : '-'],
    ['CPU 사용률',      d.cpu_percent != null ? `${d.cpu_percent.toFixed(1)}%` : '-'],
    ['메모리 사용',     d.mem_mb != null ? `${d.mem_mb.toFixed(0)} MB` : '-'],
    ['디스크 여유',     d.disk_free_gb != null ? `${d.disk_free_gb.toFixed(2)} GB` : '-'],
    ['데몬 가동시간',   fmtUptime(d.daemon_uptime)],
    ['마지막 하트비트', d.last_heartbeat ? new Date(d.last_heartbeat).toLocaleString('ko-KR') : '-'],
    ['OS',              d.os_info || '-'],
    ['커널',            d.kernel_version || '-'],
    ['proftpd',         d.proftpd_version || '-'],
    ['데몬 버전',       d.daemon_version || '-'],
  ];
  document.getElementById('dsBody').innerHTML = `
    <table class="table table-sm">
      <tbody>${rows.map(([k,v])=>`<tr><th class="text-muted fw-normal" style="width:140px">${k}</th><td>${v}</td></tr>`).join('')}</tbody>
    </table>`;
  new bootstrap.Modal(document.getElementById('deviceStatusModal')).show();
}

async function confirmDevice(id) {
  await api('PUT', `/devices/${id}/status`, {status:'confirmed'});
  loadDevices();
}
async function disableDevice(id) {
  await api('PUT', `/devices/${id}/status`, {status:'disabled'});
  loadDevices();
}
async function enableDevice(id) {
  await api('PUT', `/devices/${id}/status`, {status:'confirmed'});
  loadDevices();
}
async function deleteDevice(id) {
  if (!confirm('장비를 삭제하시겠습니까? 관련 로그도 모두 삭제됩니다.')) return;
  await api('DELETE', `/devices/${id}`);
  loadDevices();
}

async function requestDaemonUpdate(id) {
  const d = _deviceCache.find(x => x.id === id);
  const name = d?.hostname || `#${id}`;
  if (!confirm(`${name} 데몬을 업데이트하시겠습니까?\n다음 하트비트(30초 내)에 자동으로 최신 버전을 다운로드하고 재시작합니다.`)) return;
  try {
    await api('POST', `/devices/${id}/update`);
    alert(`${name}: 업데이트 요청이 전송되었습니다.\n다음 하트비트에서 데몬이 자동으로 재시작됩니다.`);
    loadDevices();
  } catch(e) { alert('업데이트 요청 실패: ' + e.message); }
}

async function openDeviceGroups(deviceId, hostname, currentGroupIds) {
  document.getElementById('dgDeviceId').value = deviceId;
  document.getElementById('dgDeviceName').textContent = hostname;
  const groups = await api('GET', '/groups');
  if (!groups) return;
  allGroups = groups;
  const list = document.getElementById('dgGroupList');
  list.innerHTML = groups.map(g => `
    <div class="form-check">
      <input class="form-check-input" type="checkbox" id="dgg${g.id}" value="${g.id}"
        ${currentGroupIds.includes(g.id)?'checked':''}>
      <label class="form-check-label" for="dgg${g.id}">
        ${g.telco ? `<span class="badge bg-info text-dark me-1">${g.telco}</span>` : ''}
        ${g.name}
      </label>
    </div>
  `).join('');
  new bootstrap.Modal(document.getElementById('deviceGroupModal')).show();
}

async function saveDeviceGroups() {
  const id = document.getElementById('dgDeviceId').value;
  const checked = [...document.querySelectorAll('#dgGroupList input:checked')].map(el=>parseInt(el.value));
  await api('PUT', `/devices/${id}/groups`, {group_ids: checked});
  bootstrap.Modal.getInstance(document.getElementById('deviceGroupModal')).hide();
  loadDevices();
}

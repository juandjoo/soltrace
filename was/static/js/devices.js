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
    // 아래 둘은 서버가 하트비트까지 보고 정한 값(effective_daemon_status) — 120초 넘게 소식 없음
    stopped:  {cls:'danger',   icon:'bi-stop-circle-fill', label:'종료됨'},
    lost:     {cls:'danger',   icon:'bi-wifi-off', label:'응답 없음'},
    disabled: {cls:'secondary',icon:'bi-slash-circle', label:'비활성'},
    unknown:  {cls:'light',    icon:'bi-question-circle', label:'미확인'},
  };
  const c = cfg[s] || cfg.unknown;
  const tip = errMsg ? ` title="${esc(errMsg)}" data-bs-toggle="tooltip"` : '';
  return `<span class="badge bg-${c.cls} text-${s==='unknown'?'dark':''}"${tip}><i class="bi ${c.icon} me-1"></i>${c.label}</span>`;
}

// 소속 그룹 배지 — 통신사를 그룹명 앞에 함께 보여준다. 같은 이름의 그룹이 통신사별로
// 있을 수 있어 그룹명만으로는 어느 쪽인지 알 수 없다 (그룹 배정 모달도 텔코를 보여준다).
function groupBadge(g) {
  const telco = g.telco
    ? `<span class="text-primary-emphasis fw-semibold">${esc(g.telco)}</span> · `
    : '';
  const title = g.telco ? `${g.telco} · ${g.name}` : g.name;
  return `<span class="badge bg-light text-dark border me-1 fw-normal" title="${esc(title)}">${telco}${esc(g.name)}</span>`;
}

function showDeviceStatus(id) {
  const d = _deviceCache.find(x => x.id === id);
  if (!d) return;
  document.getElementById('dsHostname').textContent = d.hostname;
  const rows = [
    ['데몬 상태',       daemonBadge(d.effective_daemon_status || d.daemon_status || 'unknown', d.error_message)],
    ['에러 메시지',     d.error_message ? `<span class="text-danger">${esc(d.error_message)}</span>` : '-'],
    ['마지막 전송',     d.last_send_time ? new Date(d.last_send_time).toLocaleString('ko-KR') : '-'],
    ['연속 실패',       d.consecutive_failures != null ? `${d.consecutive_failures}회` : '-'],
    ['버퍼 대기',       d.buffer_lines != null ? `${d.buffer_lines.toLocaleString()}건` : '-'],
    ['큐 대기',         d.queue_size != null ? `${d.queue_size}건` : '-'],
    ['CPU 사용률',      d.cpu_percent != null ? `${d.cpu_percent.toFixed(1)}%` : '-'],
    ['메모리 사용',     d.mem_mb != null ? `${d.mem_mb.toFixed(0)} MB` : '-'],
    ['디스크 여유',     d.disk_free_gb != null ? `${d.disk_free_gb.toFixed(2)} GB` : '-'],
    ['데몬 가동시간',   fmtUptime(d.daemon_uptime)],
    ['마지막 하트비트', d.last_heartbeat ? new Date(d.last_heartbeat).toLocaleString('ko-KR') : '-'],
    ['OS',              esc(d.os_info || '-')],
    ['커널',            esc(d.kernel_version || '-')],
    ['proftpd',         esc(d.proftpd_version || '-')],
    ['데몬 버전',       esc(d.daemon_version || '-') + (d.daemon_outdated ? ' <span class="badge bg-warning text-dark ms-1">구버전</span>' : '')],
  ];
  document.getElementById('dsBody').innerHTML = `
    <table class="table table-sm">
      <tbody>${rows.map(([k,v])=>`<tr><th class="text-muted fw-normal" style="width:140px">${k}</th><td>${v}</td></tr>`).join('')}</tbody>
    </table>`;
  bootstrap.Modal.getOrCreateInstance(document.getElementById('deviceStatusModal')).show();
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


// ── 체크한 장비 일괄 작업 (오른쪽 표 위 막대) ───────────────────────────────

function pickedDeviceIds() {
  return [...document.querySelectorAll('#dgRight .dev-pick:checked')].map(el => parseInt(el.value));
}

function _pickedPreview(ids) {
  const names = ids.map(id => _deviceCache.find(d => d.id === id)?.hostname || `#${id}`);
  return names.slice(0, 5).join(', ') + (names.length > 5 ? ` 외 ${names.length - 5}대` : '');
}

async function requestPickedDaemonUpdate() {
  const ids = pickedDeviceIds();
  if (!ids.length) return;
  if (!confirm(`장비 ${ids.length}대의 데몬을 업데이트하시겠습니까?\n${_pickedPreview(ids)}\n\n각 장비의 다음 하트비트에서 최신 데몬을 내려받고 재시작합니다.`)) return;
  try {
    const r = await api('POST', '/devices/bulk-update', {device_ids: ids});
    alert(`${r?.requested ?? ids.length}대에 업데이트 요청이 전송되었습니다.`);
    loadDevices();
  } catch (e) { alert('업데이트 요청 실패: ' + e.message); }
}

async function disablePickedDevices() {
  const ids = pickedDeviceIds();
  if (!ids.length) return;
  if (!confirm(`장비 ${ids.length}대를 비활성화하시겠습니까?\n${_pickedPreview(ids)}\n\n비활성 장비의 로그는 받지 않습니다.`)) return;
  try {
    for (const id of ids) await api('PUT', `/devices/${id}/status`, {status: 'disabled'});
  } catch (e) { alert('비활성화 실패: ' + e.message); }
  loadDevices();
}

// ── 그룹 이동 — 장비 하나(행 메뉴) 또는 체크한 여러 대 ──────────────────────
// 장비는 그룹 하나에만 둔다(운영 데이터상 여러 그룹 소속이 0대 — 2026-11-17).
// 서버 매핑(device_groups)은 여러 그룹을 허용하므로, 이동은 '그 그룹 하나로 바꾸기'다.
let _moveIds = [];

function openDeviceMove(ids) {
  _moveIds = ids.length ? ids : pickedDeviceIds();
  if (!_moveIds.length) return;
  document.getElementById('dmTitle').textContent = _moveIds.length === 1
    ? (_deviceCache.find(d => d.id === _moveIds[0])?.hostname || '') : `${_moveIds.length}대`;
  const cur = _deviceCache.find(d => d.id === _moveIds[0])?.groups?.[0]?.id;
  const telcos = [...new Set(allGroups.map(g => g.telco || ''))].sort((a, b) => (!a) - (!b) || a.localeCompare(b));
  document.getElementById('dmGroup').innerHTML = '<option value="">(그룹 없음)</option>' + telcos.map(t =>
    `<optgroup label="${esc(t || '통신사 미지정')}">${allGroups.filter(g => (g.telco || '') === t)
      .map(g => `<option value="${g.id}" ${g.id === cur ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}</optgroup>`).join('');
  bootstrap.Modal.getOrCreateInstance(document.getElementById('deviceMoveModal')).show();
}

async function saveDeviceMove() {
  const gid = document.getElementById('dmGroup').value;
  const groupIds = gid ? [parseInt(gid)] : [];
  try {
    for (const id of _moveIds) await api('PUT', `/devices/${id}/groups`, {group_ids: groupIds});
    bootstrap.Modal.getInstance(document.getElementById('deviceMoveModal')).hide();
  } catch (e) { alert('그룹 이동 실패: ' + e.message); }
  loadDevices();
}

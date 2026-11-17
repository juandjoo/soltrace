// ── 장비·그룹 화면 — 왼쪽 그룹 목록 + 오른쪽 장비 표 ──────────────────────
// 예전 '장비 관리'·'그룹 관리' 두 화면을 하나로 합쳤다(2026-11-17). 운영 장비는 모두 그룹
// 하나에만 속해 있어 그룹이 사실상 장비 폴더다 — 그룹을 고르면 그 장비와 그룹 작업
// (설정·재전송·전체 업데이트)이 한 화면에 있다. 장비별 동작은 devices.js, 그룹 설정 모달은
// groups.js, 재전송 모달은 backfill.js 가 그대로 맡는다.

let _deviceCache = [];
let _dvView = 'all';      // 'all' | 'pending' | 'disabled' | 'none' | 그룹 id(숫자)

// 접어 둔 통신사 — 보는 사람마다의 편의 설정이라 브라우저에만 둔다(없거나 막혀 있어도 펼친 채로 동작)
const DV_COLLAPSE_KEY = 'soltrace_dv_collapsed_telcos';
let _dvCollapsed = new Set();
try { _dvCollapsed = new Set(JSON.parse(localStorage.getItem(DV_COLLAPSE_KEY) || '[]')); } catch (e) { /* 펼친 채로 */ }

function toggleTelco(key) {
  _dvCollapsed.has(key) ? _dvCollapsed.delete(key) : _dvCollapsed.add(key);
  try { localStorage.setItem(DV_COLLAPSE_KEY, JSON.stringify([..._dvCollapsed])); } catch (e) { /* 이번 화면에서만 */ }
  renderDeviceSide();
}

const DV_SPECIAL = {
  all:      {label: '전체 장비', sub: '', filter: () => true},
  pending:  {label: '승인 대기', sub: '새로 등록된 장비', filter: d => d.status === 'pending'},
  disabled: {label: '비활성', sub: '로그를 받지 않는 장비', filter: d => d.status === 'disabled'},
  none:     {label: '미배정', sub: '그룹 없는 장비', filter: d => !d.groups.length},
};

async function loadDevices() {
  let devices, groups;
  try {
    [devices, groups] = await Promise.all([api('GET', '/devices'), api('GET', '/groups')]);
  } catch (e) {
    document.getElementById('dgRight').innerHTML = `<div class="alert alert-danger small">불러오기 실패: ${esc(e.message)}</div>`;
    return;
  }
  if (!devices || !groups) return;
  _deviceCache = devices;
  allGroups = groups;
  if (typeof _dvView === 'number' && !groups.some(g => g.id === _dvView)) _dvView = 'all';
  renderDeviceSide();
  renderDeviceRight();
}

function selectDeviceView(key) {
  _dvView = key in DV_SPECIAL ? key : parseInt(key);
  renderDeviceSide();
  renderDeviceRight();
}

function _devicesIn(view) {
  if (view in DV_SPECIAL) return _deviceCache.filter(DV_SPECIAL[view].filter);
  return _deviceCache.filter(d => d.groups.some(g => g.id === view));
}

// 장비 하나의 상태 점 — 끊김·오류는 빨강, 저하·구버전·버퍼 밀림은 노랑
function _deviceHealth(d) {
  if (d.status === 'disabled') return '';
  const s = d.effective_daemon_status || d.daemon_status;
  if (['error', 'stopped', 'lost'].includes(s)) return 'bad';
  if (s === 'degraded' || d.daemon_outdated || d.buffer_lines > 0) return 'warn';
  return 'ok';
}

function _worstHealth(list) {
  const hs = list.map(_deviceHealth);
  return hs.includes('bad') ? 'bad' : hs.includes('warn') ? 'warn' : hs.includes('ok') ? 'ok' : '';
}

function _customerSummary(g) {
  const lines = (g.customer || '').split('\n').map(x => x.trim()).filter(Boolean);
  if (!lines.length) return '';
  return lines[0] + (lines.length > 1 ? ` 외 ${lines.length - 1}` : '');
}

function _dvQuery() {
  return (document.getElementById('dgSearch')?.value || '').trim().toLowerCase();
}

const _hostMatch = (d, q) => `${d.hostname} ${d.ip_address || ''}`.toLowerCase().includes(q);

function _groupMatch(g, q) {
  return !q || g.name.toLowerCase().includes(q) || (g.customer || '').toLowerCase().includes(q)
    || _devicesIn(g.id).some(d => _hostMatch(d, q));
}

function _telcoKeys() {
  return [...new Set(allGroups.map(g => g.telco || ''))].sort((a, b) => (!a) - (!b) || a.localeCompare(b));
}

function renderDeviceSide() {
  const q = _dvQuery();
  const item = (key, name, sub, n, health, extra = '') => `
    <div class="dg-item ${String(_dvView) === String(key) ? 'active' : ''}" onclick="selectDeviceView('${key}')" title="${esc(name)}">
      <span class="dg-dot ${health}"></span>
      <div class="flex-grow-1 overflow-hidden"><div class="dg-nm">${esc(name)}</div>${sub ? `<div class="dg-sub">${esc(sub)}</div>` : ''}</div>
      <span class="badge ${extra || 'bg-light text-dark border'}">${n}</span>
    </div>`;
  let html = '';
  for (const [key, v] of Object.entries(DV_SPECIAL)) {
    const list = _devicesIn(key);
    // 비활성·미배정은 있을 때만 — 늘 0 인 항목이 목록을 차지하지 않게. 승인 대기는 늘 보인다.
    if (!list.length && (key === 'disabled' || key === 'none')) continue;
    const warn = key === 'pending' && list.length ? 'bg-warning text-dark' : '';
    html += item(key, v.label, v.sub, list.length, key === 'all' ? _worstHealth(list) : '', warn);
  }
  for (const t of _telcoKeys()) {
    const gs = allGroups.filter(g => (g.telco || '') === t && _groupMatch(g, q));
    if (!gs.length) continue;
    // 검색 중에는 접힘을 무시한다 — 찾은 것이 접힌 곳에 숨으면 '결과 없음'으로 보인다
    const collapsed = !q && _dvCollapsed.has(t);
    const devices = gs.reduce((n, g) => n + g.device_count, 0);
    const health = _worstHealth(gs.flatMap(g => _devicesIn(g.id)));
    html += `<div class="dg-telco" role="button" data-telco="${esc(t)}" onclick="toggleTelco(this.dataset.telco)" title="${collapsed ? '펼치기' : '접기'}">
      <i class="bi bi-chevron-${collapsed ? 'right' : 'down'} me-1"></i>${t ? `<i class="bi bi-broadcast-pin me-1 text-brand"></i>${esc(t)}` : '통신사 미지정'}
      <span class="fw-normal">· ${gs.length}그룹 · ${devices}대</span>
      ${collapsed ? `<span class="dg-dot ${health} ms-auto"></span>` : ''}</div>`;
    // 접혀 있어도 지금 고른 그룹은 남긴다 — 오른쪽에 보이는 그룹이 왼쪽에서 사라지지 않게
    const shown = collapsed ? gs.filter(g => g.id === _dvView) : gs;
    html += shown.map(g => item(g.id, g.name, _customerSummary(g) || '고객사 없음', g.device_count,
                                _worstHealth(_devicesIn(g.id)))).join('');
  }
  document.getElementById('dgSide').innerHTML = html || '<div class="text-center text-muted py-4 small">검색 결과가 없습니다.</div>';

  const opt = (key, label) => `<option value="${key}" ${String(_dvView) === String(key) ? 'selected' : ''}>${esc(label)}</option>`;
  document.getElementById('dgMobilePick').innerHTML =
    Object.entries(DV_SPECIAL).map(([k, v]) => opt(k, `${v.label} (${_devicesIn(k).length})`)).join('') +
    _telcoKeys().map(t => `<optgroup label="${esc(t || '통신사 미지정')}">${allGroups.filter(g => (g.telco || '') === t)
      .map(g => opt(g.id, `${g.name} (${g.device_count})`)).join('')}</optgroup>`).join('');
}

function _groupHeader(g) {
  const cust = _customerSummary(g);
  const n = g.device_count;
  return `<div class="card mb-3"><div class="card-body py-2 px-3">
    <div class="d-flex align-items-center gap-2 flex-wrap">
      ${g.telco ? `<span class="badge bg-info text-dark">${esc(g.telco)}</span>` : ''}
      <span class="fw-bold">${esc(g.name)}</span>${purgeBadge(g)}
      <span class="small text-muted" title="${esc(g.customer || '')}">${cust ? esc(cust) : '고객사 없음'}</span>
      <div class="ms-auto d-flex gap-1 flex-wrap">
        <button class="btn btn-xs btn-outline-primary" onclick="openGroupModal(${g.id})"><i class="bi bi-pencil me-1"></i>설정</button>
        <button class="btn btn-xs btn-outline-secondary" onclick="openBackfill(${g.id})" ${n ? '' : 'disabled'} title="장비에 남은 로그 중 빠진 것을 다시 받기"><i class="bi bi-cloud-arrow-up me-1"></i>재전송</button>
        <button class="btn btn-xs btn-outline-success" onclick="requestGroupDaemonUpdate(${g.id})" ${n ? '' : 'disabled'} title="이 그룹 장비의 데몬을 한 번에 업데이트"><i class="bi bi-arrow-repeat me-1"></i>전체 업데이트</button>
        <div class="dropdown">
          <button class="btn btn-xs btn-outline-secondary" data-bs-toggle="dropdown" data-bs-popper-config='{"strategy":"fixed"}' title="더보기"><i class="bi bi-three-dots"></i></button>
          <ul class="dropdown-menu dropdown-menu-end small"><li><a class="dropdown-item text-danger" href="#" onclick="deleteGroup(${g.id});return false"><i class="bi bi-trash me-2"></i>그룹 삭제</a></li></ul>
        </div>
      </div>
    </div>
    ${g.upload_domains || g.description ? `<div class="small text-muted mt-1 text-truncate" title="${esc([g.upload_domains, g.description].filter(Boolean).join('\n'))}">${esc((g.upload_domains || '').split('\n')[0])}${g.description ? ` · ${esc(g.description.split('\n')[0])}` : ''}</div>` : ''}
  </div></div>`;
}

function _deviceRowMenu(d) {
  const it = (icon, label, js, cls = '') => `<li><a class="dropdown-item ${cls}" href="#" onclick="${js};return false"><i class="bi ${icon} me-2"></i>${label}</a></li>`;
  return `<div class="dropdown d-inline-block">
    <button class="btn btn-xs btn-outline-secondary" data-bs-toggle="dropdown" data-bs-popper-config='{"strategy":"fixed"}' title="더보기"><i class="bi bi-three-dots"></i></button>
    <ul class="dropdown-menu dropdown-menu-end small">
      ${it('bi-arrow-repeat', d.update_requested ? '데몬 업데이트 (요청됨)' : '데몬 업데이트', `requestDaemonUpdate(${d.id})`)}
      ${it('bi-folder-symlink', '그룹 이동', `openDeviceMove([${d.id}])`)}
      ${d.status === 'confirmed' ? it('bi-pause-circle', '비활성', `disableDevice(${d.id})`) : ''}
      ${d.status === 'disabled' ? it('bi-play-circle', '활성화', `enableDevice(${d.id})`) : ''}
      <li><hr class="dropdown-divider"></li>
      ${it('bi-trash', '삭제', `deleteDevice(${d.id})`, 'text-danger')}
    </ul></div>`;
}

function _deviceRow(d, showGroup) {
  // 끊김 판정은 서버(app/device_health) 한 곳 — 대시보드·알림과 같은 규칙
  const dStatus = d.effective_daemon_status || d.daemon_status || 'unknown';
  const offline = dStatus === 'stopped' || dStatus === 'lost';
  const errMsg = offline ? `마지막 하트비트: ${timeAgo(d.last_heartbeat)}` : d.error_message;
  // 메모리는 장비 총량을 모르니(사용량만 보고) 막대 없이 값만 — 512MB 기준 막대가 전부 빨갛던 문제
  const mem = d.mem_mb != null ? (d.mem_mb >= 1024 ? `${(d.mem_mb / 1024).toFixed(1)}G` : `${Math.round(d.mem_mb)}M`) : '-';
  const cpu = d.cpu_percent != null ? `<span class="${d.cpu_percent >= 90 ? 'text-danger fw-semibold' : d.cpu_percent >= 70 ? 'text-warning fw-semibold' : ''}">${d.cpu_percent.toFixed(1)}%</span>` : '-';
  return `<tr class="${dStatus === 'error' ? 'table-danger-subtle' : ''}${d.status === 'disabled' ? ' text-muted' : ''}">
    <td class="text-center"><input class="form-check-input dev-pick" type="checkbox" value="${d.id}" onchange="updateDevicePickCount()"></td>
    <td>
      <strong>${esc(d.hostname)}</strong>${d.status !== 'confirmed' ? ' ' + statusBadge(d.status) : ''}
      <div style="font-size:11px" class="${d.daemon_outdated ? 'text-warning fw-semibold' : 'text-muted'}">${esc(d.daemon_version || '')}${d.daemon_outdated ? ' <i class="bi bi-exclamation-triangle-fill" data-bs-toggle="tooltip" title="구버전 — 데몬 업데이트가 필요합니다"></i>' : ''}</div>
    </td>
    <td class="text-muted small dg-hide-sm">${esc(d.ip_address || '-')}</td>
    <td class="text-center">${daemonBadge(dStatus, errMsg)}<div class="text-muted" style="font-size:11px">${fmtUptime(d.daemon_uptime)} 가동</div></td>
    <td class="small text-nowrap dg-hide-sm">${cpu} · ${mem}${d.disk_free_gb != null ? ` · 💾${d.disk_free_gb.toFixed(1)}G` : ''}</td>
    <td class="text-center small dg-hide-sm">
      ${d.buffer_lines > 0 ? `<span class="badge bg-warning text-dark">${d.buffer_lines.toLocaleString()}건</span>` : '<span class="text-muted">-</span>'}
      ${d.queue_size > 0 ? `<span class="badge bg-info text-dark ms-1">Q:${d.queue_size}</span>` : ''}
    </td>
    <td class="text-muted small dg-hide-sm">${timeAgo(d.last_heartbeat)}</td>
    ${showGroup ? `<td class="dg-hide-sm">${d.groups.map(g => `<a href="#" class="text-decoration-none" onclick="selectDeviceView(${g.id});return false">${groupBadge(g)}</a>`).join('') || '<span class="text-muted small">-</span>'}</td>` : ''}
    <td class="text-end text-nowrap">
      ${d.status === 'pending' ? `<button class="btn btn-xs btn-success me-1" onclick="confirmDevice(${d.id})">승인</button>` : ''}
      <button class="btn btn-xs btn-outline-info me-1" onclick="showDeviceStatus(${d.id})">상태</button>${_deviceRowMenu(d)}
    </td>
  </tr>`;
}

function renderDeviceRight() {
  const right = document.getElementById('dgRight');
  // 이전 렌더의 툴팁을 먼저 정리한다. Bootstrap 은 인스턴스를 element 키의 Map(강한 참조)에
  // 담으므로, dispose 없이 innerHTML 을 갈아끼우면 떨어져 나간 DOM 이 계속 붙잡혀 있다.
  right.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => bootstrap.Tooltip.getInstance(el)?.dispose());

  const g = typeof _dvView === 'number' ? allGroups.find(x => x.id === _dvView) : null;
  const q = _dvQuery();
  // 검색어가 그룹명·고객사에 걸리면 그 그룹 장비는 다 보이고, 아니면 호스트명·IP 로 거른다
  const groupHit = g && q && (g.name.toLowerCase().includes(q) || (g.customer || '').toLowerCase().includes(q));
  const all = _devicesIn(_dvView);
  const list = !q || groupHit ? all : all.filter(d => _hostMatch(d, q));
  const showGroup = !g;
  const head = g ? _groupHeader(g) : `
    <div class="d-flex align-items-center mb-2 flex-wrap gap-2"><span class="fw-bold">${DV_SPECIAL[_dvView].label}</span>
      <span class="small text-muted">${list.length}대${list.length !== all.length ? ` / ${all.length}대` : ''} · 그룹을 고르면 그룹 설정·재전송·전체 업데이트가 함께 나옵니다</span></div>`;
  const empty = _dvView === 'pending' ? '승인 대기 중인 장비가 없습니다.' : q ? '검색 결과가 없습니다.' : '장비가 없습니다.';
  right.innerHTML = head + `
    <div class="dg-bulk d-none align-items-center gap-2 px-3 py-2 mb-2 flex-wrap" id="dgBulk">
      <span class="small fw-semibold"><span id="devPickCount">0</span>대 선택</span>
      <div class="ms-auto d-flex gap-1">
        <button class="btn btn-xs btn-outline-success" onclick="requestPickedDaemonUpdate()"><i class="bi bi-arrow-repeat me-1"></i>업데이트</button>
        <button class="btn btn-xs btn-outline-secondary" onclick="openDeviceMove([])"><i class="bi bi-folder-symlink me-1"></i>그룹 이동</button>
        <button class="btn btn-xs btn-outline-warning" onclick="disablePickedDevices()"><i class="bi bi-pause-circle me-1"></i>비활성</button>
      </div>
    </div>
    <div class="card"><div class="table-responsive"><table class="table table-hover align-middle mb-0">
      <thead class="table-light"><tr>
        <th class="text-center" style="width:38px"><input class="form-check-input" type="checkbox" id="devPickAll" onchange="togglePickAllDevices(this.checked)" title="전체 선택"></th>
        <th>호스트명</th><th class="dg-hide-sm">IP</th><th class="text-center">데몬</th><th class="dg-hide-sm">CPU · MEM · Disk</th>
        <th class="text-center dg-hide-sm">버퍼</th><th class="dg-hide-sm">마지막 연결</th>${showGroup ? '<th class="dg-hide-sm">그룹</th>' : ''}<th></th>
      </tr></thead>
      <tbody>${list.map(d => _deviceRow(d, showGroup)).join('') || `<tr><td colspan="9" class="text-center text-muted py-4">${empty}</td></tr>`}</tbody>
    </table></div></div>`;
  // 방금 만든 행에만 붙인다
  right.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el, {placement: 'top'}));
  updateDevicePickCount();
}

function updateDevicePickCount() {
  const n = pickedDeviceIds().length;
  const total = document.querySelectorAll('#dgRight .dev-pick').length;
  const bar = document.getElementById('dgBulk');
  if (!bar) return;
  bar.classList.toggle('d-none', !n);
  bar.classList.toggle('d-flex', !!n);
  document.getElementById('devPickCount').textContent = n;
  const all = document.getElementById('devPickAll');
  if (all) { all.checked = total > 0 && n === total; all.indeterminate = n > 0 && n < total; }
}

function togglePickAllDevices(checked) {
  document.querySelectorAll('#dgRight .dev-pick').forEach(el => { el.checked = checked; });
  updateDevicePickCount();
}

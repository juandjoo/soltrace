let _groupFilter = '__all__';
const GROUP_COLS = 8;   // 체크 열 포함

async function loadGroups() {
  const groups = await api('GET', '/groups');
  if (!groups) return;
  allGroups = groups;
  const container = document.getElementById('groupCards');
  if (!groups.length) {
    container.innerHTML = '<div class="text-center text-muted py-4">그룹이 없습니다. 추가해주세요.</div>';
    document.getElementById('groupPager').innerHTML = '';
    updateGroupPickCount();
    return;
  }

  const byTelco = {};
  groups.forEach(g => {
    const key = g.telco || '\x00';
    if (!byTelco[key]) byTelco[key] = [];
    byTelco[key].push(g);
  });
  const telcoKeys = Object.keys(byTelco).sort((a, b) => {
    if (a === '\x00') return 1; if (b === '\x00') return -1;
    return a.localeCompare(b);
  });

  const filterEl = document.getElementById('groupFilter');
  const filterKeys = [null, ...telcoKeys.filter(k => k !== '\x00'), '\x00'];
  filterEl.innerHTML = filterKeys.map(key => {
    const label = key === null ? '전체' : key === '\x00' ? '미지정' : esc(key);
    const icon  = key === null ? 'bi-list-ul' : key === '\x00' ? 'bi-dash-circle' : 'bi-broadcast-pin-fill';
    const active = (_groupFilter === (key ?? '__all__')) ? 'active' : '';
    return `<button class="btn btn-sm btn-outline-secondary ${active}" data-filter="${key ?? '__all__'}" onclick="filterGroups(this)"><i class="bi ${icon} me-1"></i>${label}</button>`;
  }).join('');

  window._groupByTelco = byTelco;
  window._groupTelcoKeys = telcoKeys;
  renderGroupPage(1);
}

function _filteredGroups() {
  const byTelco = window._groupByTelco || {};
  if (_groupFilter === '__all__') return allGroups;
  const key = _groupFilter === '' ? '\x00' : _groupFilter;
  return byTelco[key] || [];
}

function renderGroupPage(page) {
  const container = document.getElementById('groupCards');
  const pager = document.getElementById('groupPager');
  const psEl = document.getElementById('groupPageSize');
  const pageSize = psEl ? parseInt(psEl.value) : 20;
  const byTelco = window._groupByTelco || {};
  const telcoKeys = window._groupTelcoKeys || [];

  const filtered = _filteredGroups();
  const total = filtered.length;

  const useAll = pageSize === 0 || total <= pageSize;
  const totalPages = useAll ? 1 : Math.ceil(total / pageSize);
  page = Math.max(1, Math.min(page, totalPages));
  const start = useAll ? 0 : (page - 1) * pageSize;
  const end   = useAll ? total : Math.min(start + pageSize, total);
  const pageItems = filtered.slice(start, end);
  const pageIds = new Set(pageItems.map(g => g.id));

  const groupRowHtml = g => `
      <td class="text-center"><input class="form-check-input grp-pick" type="checkbox" value="${g.id}" onchange="updateGroupPickCount()"></td>
      <td class="fw-semibold" style="word-break:break-word">${esc(g.name)}</td>
      <td class="text-center"><span class="badge bg-light text-dark border">${g.device_count}대</span></td>
      <td class="small" style="white-space:pre-wrap;word-break:break-word">${g.customer ? esc(g.customer) : '<span class="text-muted">-</span>'}</td>
      <td class="small text-muted" style="white-space:pre-wrap;word-break:break-all">${g.upload_domains ? esc(g.upload_domains) : '-'}</td>
      <td class="small" style="word-break:break-word;white-space:pre-wrap">${g.application ? esc(g.application) : '<span class="text-muted">-</span>'}</td>
      <td class="small text-muted" style="word-break:break-word;white-space:pre-wrap">${g.description ? esc(g.description) : '-'}</td>
      <td><div class="d-flex gap-1 justify-content-end">
        <button class="btn btn-xs btn-outline-secondary" onclick="openGroupDevices(${g.id})" title="이 그룹에 장비 등록"><i class="bi bi-hdd-network me-1"></i>장비</button>
        <button class="btn btn-xs btn-outline-success" onclick="requestGroupDaemonUpdate(${g.id})" title="이 그룹 장비의 데몬을 한 번에 업데이트" ${g.device_count ? '' : 'disabled'}><i class="bi bi-arrow-repeat"></i></button>
        <button class="btn btn-xs btn-outline-primary" onclick="openGroupModal(${g.id})">수정</button>
        <button class="btn btn-xs btn-outline-danger" onclick="deleteGroup(${g.id})"><i class="bi bi-trash"></i></button>
      </div></td>`;

  let isFirst = true;
  const sectionRows = telcoKeys.map(key => {
    const telco = key === '\x00' ? null : key;
    const list  = (byTelco[key] || []).filter(g => pageIds.has(g.id));
    if (!list.length) return '';
    const total = list.reduce((s, g) => s + g.device_count, 0);
    const headerLabel = telco
      ? `<i class="bi bi-broadcast-pin-fill me-2 text-brand"></i><span class="fw-semibold">${esc(telco)}</span>`
      : `<i class="bi bi-dash-circle me-2 text-muted"></i><span class="fw-semibold text-muted">통신사 미지정</span>`;
    const spacer = isFirst ? '' : `<tr><td colspan="${GROUP_COLS}" style="height:10px;padding:0;background:var(--st-bg);border-top:2px solid var(--st-border)"></td></tr>`;
    isFirst = false;
    return `${spacer}
      <tr class="table-secondary" data-telco="${telco || ''}">
        <td colspan="${GROUP_COLS}" class="py-2">
          ${headerLabel}
          <span class="ms-2 badge bg-secondary">${list.length}그룹</span>
          <span class="ms-1 badge bg-light text-dark border">${total}대</span>
        </td>
      </tr>
      ${list.map(g => `<tr data-telco="${telco || ''}">${groupRowHtml(g)}</tr>`).join('')}`;
  }).join('');

  container.innerHTML = `
    <div class="card">
      <div class="table-responsive">
        <table class="table table-hover align-middle mb-0" style="table-layout:fixed">
          <colgroup>
            <col style="width:4%"><col style="width:14%"><col style="width:6%"><col style="width:14%">
            <col style="width:15%"><col style="width:13%"><col style="width:14%"><col style="width:20%">
          </colgroup>
          <thead class="table-light">
            <tr><th class="text-center"><input class="form-check-input" type="checkbox" id="grpPickAll" onchange="togglePickAllGroups(this.checked)" title="이 페이지 전체 선택"></th>
            <th>그룹명</th><th class="text-center">장비</th><th>고객사</th>
            <th>업로드 도메인</th><th>서비스</th><th>비고</th><th></th></tr>
          </thead>
          <tbody>${sectionRows || `<tr><td colspan="${GROUP_COLS}" class="text-center text-muted py-3">해당 항목이 없습니다.</td></tr>`}</tbody>
        </table>
      </div>
      ${total > 0 ? `<div class="px-3 py-1 border-top small text-muted">${start+1}–${end} / ${total}그룹</div>` : ''}
    </div>`;
  updateGroupPickCount();   // 다시 그리면 선택이 풀린다 — 버튼/헤더 체크도 같이 되돌린다

  if (totalPages <= 1) { pager.innerHTML = ''; return; }
  const pages = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || Math.abs(i - page) <= 2)
      pages.push(i);
    else if (pages[pages.length-1] !== '…')
      pages.push('…');
  }
  pager.innerHTML = `<ul class="pagination pagination-sm mb-0">
    <li class="page-item${page===1?' disabled':''}"><a class="page-link" onclick="renderGroupPage(${page-1})">‹</a></li>
    ${pages.map(p => p === '…'
      ? `<li class="page-item disabled"><span class="page-link">…</span></li>`
      : `<li class="page-item${p===page?' active':''}"><a class="page-link" onclick="renderGroupPage(${p})">${p}</a></li>`
    ).join('')}
    <li class="page-item${page===totalPages?' disabled':''}"><a class="page-link" onclick="renderGroupPage(${page+1})">›</a></li>
  </ul>`;
}

function filterGroups(btn) {
  document.querySelectorAll('#groupFilter button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _groupFilter = btn.dataset.filter;
  renderGroupPage(1);
}

function populateGroupTelco(selected) {
  const sel = document.getElementById('groupTelco');
  const opts = ['<option value="">(통신사 미지정)</option>']
    .concat(allTelcos.map(t => `<option value="${esc(t.name)}"${t.name===selected?' selected':''}>${esc(t.name)}</option>`));
  sel.innerHTML = opts.join('');
}

async function openGroupModal(id) {
  const g = id ? allGroups.find(x => x.id === id) : null;
  document.getElementById('groupId').value = id || '';
  document.getElementById('groupName').value = g?.name || '';
  document.getElementById('groupDesc').value = g?.description || '';
  document.getElementById('groupCustomer').value = g?.customer || '';
  document.getElementById('groupUploadDomains').value = g?.upload_domains || '';
  document.getElementById('groupApplication').value = g?.application || '';
  await getTelcos();
  populateGroupTelco(g?.telco || '');
  document.getElementById('groupModalTitle').textContent = id ? '그룹 수정' : '그룹 추가';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('groupModal')).show();
}

async function saveGroup() {
  const id = document.getElementById('groupId').value;
  const body = {
    name: document.getElementById('groupName').value.trim(),
    description: document.getElementById('groupDesc').value.trim() || null,
    telco: document.getElementById('groupTelco').value || null,
    customer: document.getElementById('groupCustomer').value.trim() || null,
    upload_domains: document.getElementById('groupUploadDomains').value.trim() || null,
    application: document.getElementById('groupApplication').value.trim() || null,
  };
  if (!body.name) return alert('그룹명을 입력하세요.');
  try {
    if (id) {
      await api('PUT', `/groups/${id}`, body);
    } else {
      await api('POST', '/groups', body);
    }
    bootstrap.Modal.getInstance(document.getElementById('groupModal')).hide();
    loadGroups();
  } catch(e) { alert(e.message); }
}

async function deleteGroup(id) {
  if (!confirm('그룹을 삭제하시겠습니까?')) return;
  await api('DELETE', `/groups/${id}`);
  loadGroups();
}

// ── 체크한 그룹 일괄 업데이트 ────────────────────────────────────────────────
// 그룹 하나짜리 ↻(requestGroupDaemonUpdate)와 같은 요청을 여러 그룹에 한 번에 건다.
// 두 그룹에 겹쳐 속한 장비는 서버에서 한 번만 센다.

function _pickedGroupIds() {
  return [...document.querySelectorAll('.grp-pick:checked')].map(el => parseInt(el.value));
}

function updateGroupPickCount() {
  const n = _pickedGroupIds().length;
  const total = document.querySelectorAll('.grp-pick').length;
  const cnt = document.getElementById('grpPickCount');
  const btn = document.getElementById('grpBulkBtn');
  if (cnt) cnt.textContent = n;
  if (btn) btn.disabled = n === 0;
  const all = document.getElementById('grpPickAll');
  if (all) { all.checked = total > 0 && n === total; all.indeterminate = n > 0 && n < total; }
}

function togglePickAllGroups(checked) {
  document.querySelectorAll('.grp-pick').forEach(el => { el.checked = checked; });
  updateGroupPickCount();
}

async function requestPickedGroupDaemonUpdate() {
  const ids = _pickedGroupIds();
  if (!ids.length) return;
  const picked = ids.map(id => allGroups.find(g => g.id === id)).filter(Boolean);
  const devices = picked.reduce((s, g) => s + (g.device_count || 0), 0);
  if (!devices) { alert('선택한 그룹에 소속 장비가 없습니다.'); return; }
  const names = picked.map(g => g.name);
  const preview = names.slice(0, 5).join(', ') + (names.length > 5 ? ` 외 ${names.length - 5}개` : '');
  if (!confirm(`그룹 ${ids.length}개(장비 ${devices}대)의 데몬을 업데이트하시겠습니까?\n${preview}\n\n각 장비의 다음 하트비트에서 최신 데몬을 내려받고 재시작합니다.\n(두 그룹에 겹쳐 속한 장비는 한 번만 요청됩니다.)`)) return;
  try {
    const r = await api('POST', '/groups/bulk-update', {group_ids: ids});
    alert(`${r?.requested ?? devices}대에 업데이트 요청이 전송되었습니다.`);
  } catch (e) { alert('업데이트 요청 실패: ' + e.message); }
}

async function requestGroupDaemonUpdate(id) {
  const g = allGroups.find(x => x.id === id);
  const name = g?.name || `#${id}`;
  const cnt = g?.device_count || 0;
  if (!cnt) { alert(`${name}: 소속 장비가 없습니다.`); return; }
  if (!confirm(`${name} 그룹의 장비 ${cnt}대를 모두 업데이트하시겠습니까?\n각 장비의 다음 하트비트에서 최신 데몬을 내려받고 재시작합니다.`)) return;
  try {
    const r = await api('POST', `/groups/${id}/update`);
    alert(`${name}: ${r?.requested ?? cnt}대에 업데이트 요청이 전송되었습니다.\n각 장비의 다음 하트비트에서 데몬이 재시작됩니다.`);
  } catch (e) { alert('업데이트 요청 실패: ' + e.message); }
}

// ── 그룹 → 장비 등록 ─────────────────────────────────────────────────────────
// 장비관리의 "그룹 배정"(saveDeviceGroups)과 같은 매핑을 그룹 쪽에서 편집한다.

async function openGroupDevices(groupId) {
  const g = allGroups.find(x => x.id === groupId);
  if (!g) return;
  document.getElementById('gdGroupId').value = groupId;
  document.getElementById('gdGroupName').textContent = g.name;
  document.getElementById('gdSearch').value = '';

  const devices = await api('GET', '/devices');
  if (!devices) return;
  // 장비관리에서 승인된(confirmed) 장비만 등록 대상. 단 이미 이 그룹에 속한 장비는
  // 상태와 무관하게 노출해야 저장 시 조용히 빠지지 않는다.
  const targets = devices
    .filter(d => d.status === 'confirmed' || d.groups.some(x => x.id === groupId))
    .sort((a, b) => a.hostname.localeCompare(b.hostname));

  const list = document.getElementById('gdDeviceList');
  if (!targets.length) {
    list.innerHTML = '<div class="text-center text-muted py-4">등록 가능한 승인된 장비가 없습니다.<br><span class="small">장비 관리에서 먼저 장비를 확인(승인)해주세요.</span></div>';
    document.getElementById('gdCount').textContent = '0';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('groupDeviceModal')).show();
    return;
  }

  list.innerHTML = targets.map(d => {
    const checked = d.groups.some(x => x.id === groupId);
    const others = d.groups.filter(x => x.id !== groupId);
    // 검색은 등록 여부와 무관하게 전체를 훑는다 — 데몬 버전으로도 찾을 수 있게 키에 넣는다
    const key = `${d.hostname} ${d.ip_address || ''} ${d.daemon_version || ''}`.toLowerCase();
    return `
    <div class="form-check border rounded px-2 py-1 gd-item" data-key="${esc(key)}" data-member="${checked ? '1' : ''}">
      <input class="form-check-input" type="checkbox" id="gdd${d.id}" value="${d.id}"
        ${checked ? 'checked' : ''} onchange="updateGroupDeviceCount()">
      <label class="form-check-label d-flex align-items-center gap-2 flex-wrap w-100" for="gdd${d.id}">
        <span class="fw-semibold">${esc(d.hostname)}</span>
        <span class="text-muted small">${esc(d.ip_address || '-')}</span>
        ${statusBadge(d.status)}
        <span class="small ${d.daemon_outdated ? 'text-warning fw-semibold' : 'text-muted'}" ${d.daemon_outdated ? 'title="구버전 — 데몬 업데이트가 필요합니다"' : ''}>v${esc(d.daemon_version || '-')}${d.daemon_outdated ? ' <i class="bi bi-exclamation-triangle-fill"></i>' : ''}</span>
        ${others.length ? `<span class="small text-muted">기존 그룹: ${others.map(x => esc(x.name)).join(', ')}</span>` : ''}
      </label>
    </div>`;
  }).join('') + '<div id="gdNoVisible" class="text-center text-muted py-4 d-none"></div>';

  updateGroupDeviceCount();
  filterGroupDevices();   // 처음엔 이 그룹 장비만 — 나머지는 검색해야 나온다
  bootstrap.Modal.getOrCreateInstance(document.getElementById('groupDeviceModal')).show();
}

function updateGroupDeviceCount() {
  const n = document.querySelectorAll('#gdDeviceList input:checked').length;
  document.getElementById('gdCount').textContent = n;
}

// 평소에는 이 그룹에 등록된 장비만 보인다 — 그룹 구성을 확인하러 여는 창이라
// 전체 장비를 늘어놓으면 정작 소속이 무엇인지 읽기 어렵다.
// 검색어를 넣으면 미등록 장비까지 전체에서 찾는다(등록 대상을 넓게 훑는 경우는 검색뿐).
// 검색으로 찾아 방금 체크한 장비는 검색어를 지워도 남는다 — 방금 한 일이 눈앞에서
// 사라지면 저장 전에 확인할 방법이 없다.
function filterGroupDevices() {
  const q = document.getElementById('gdSearch').value.trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('#gdDeviceList .gd-item').forEach(el => {
    const show = q
      ? el.dataset.key.includes(q)
      : (el.dataset.member === '1' || el.querySelector('input').checked);
    el.classList.toggle('d-none', !show);
    if (show) shown++;
  });
  const empty = document.getElementById('gdNoVisible');
  if (!empty) return;
  empty.classList.toggle('d-none', shown > 0);
  empty.innerHTML = q
    ? '검색 결과가 없습니다.'
    : '이 그룹에 등록된 장비가 없습니다.<br><span class="small">위 검색창에서 장비를 찾아 추가하세요.</span>';
}

// 검색으로 걸러진(보이는) 항목만 일괄 선택/해제한다.
function toggleGroupDevices(checked) {
  document.querySelectorAll('#gdDeviceList .gd-item:not(.d-none) input').forEach(el => {
    el.checked = checked;
  });
  updateGroupDeviceCount();
}

async function saveGroupDevices() {
  const id = document.getElementById('gdGroupId').value;
  const ids = [...document.querySelectorAll('#gdDeviceList input:checked')].map(el => parseInt(el.value));
  try {
    await api('PUT', `/groups/${id}/devices`, {device_ids: ids});
    bootstrap.Modal.getInstance(document.getElementById('groupDeviceModal')).hide();
    loadGroups();
  } catch(e) { alert(e.message); }
}

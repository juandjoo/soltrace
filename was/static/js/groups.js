let _groupFilter = '__all__';
const GROUP_COLS = 7;

async function loadGroups() {
  const groups = await api('GET', '/groups');
  if (!groups) return;
  allGroups = groups;
  const container = document.getElementById('groupCards');
  if (!groups.length) {
    container.innerHTML = '<div class="text-center text-muted py-4">그룹이 없습니다. 추가해주세요.</div>';
    document.getElementById('groupPager').innerHTML = '';
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
    const label = key === null ? '전체' : key === '\x00' ? '미지정' : key;
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
      <td class="fw-semibold" style="word-break:break-word">${g.name}</td>
      <td class="text-center"><span class="badge bg-light text-dark border">${g.device_count}대</span></td>
      <td class="small" style="white-space:pre-wrap;word-break:break-word">${g.customer || '<span class="text-muted">-</span>'}</td>
      <td class="small text-muted" style="white-space:pre-wrap;word-break:break-all">${g.upload_domains || '-'}</td>
      <td class="small" style="word-break:break-word">${g.application || '<span class="text-muted">-</span>'}</td>
      <td class="small text-muted" style="word-break:break-word">${g.description || '-'}</td>
      <td><div class="d-flex gap-1 justify-content-end">
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
      ? `<i class="bi bi-broadcast-pin-fill me-2 text-brand"></i><span class="fw-semibold">${telco}</span>`
      : `<i class="bi bi-dash-circle me-2 text-muted"></i><span class="fw-semibold text-muted">통신사 미지정</span>`;
    const spacer = isFirst ? '' : `<tr><td colspan="${GROUP_COLS}" style="height:10px;padding:0;background:#f5f7fa;border-top:2px solid #dee2e6"></td></tr>`;
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
            <col style="width:14%"><col style="width:6%"><col style="width:14%">
            <col style="width:24%"><col style="width:14%"><col style="width:17%"><col style="width:11%">
          </colgroup>
          <thead class="table-light">
            <tr><th>그룹명</th><th class="text-center">장비</th><th>고객사</th>
            <th>업로드 도메인</th><th>서비스</th><th>비고</th><th></th></tr>
          </thead>
          <tbody>${sectionRows || `<tr><td colspan="${GROUP_COLS}" class="text-center text-muted py-3">해당 항목이 없습니다.</td></tr>`}</tbody>
        </table>
      </div>
      ${total > 0 ? `<div class="px-3 py-1 border-top small text-muted">${start+1}–${end} / ${total}그룹</div>` : ''}
    </div>`;

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
    .concat(allTelcos.map(t => `<option value="${t.name}"${t.name===selected?' selected':''}>${t.name}</option>`));
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
  new bootstrap.Modal(document.getElementById('groupModal')).show();
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

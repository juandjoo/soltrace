const ACTION_KO = {upload:'업로드', download:'다운로드', delete:'삭제', rename:'이름변경', login:'로그인', logout:'로그아웃', mkdir:'폴더생성', rmdir:'폴더삭제', cwd_fail:'디렉토리 이동 실패', client_error:'클라이언트 오류'};
// 작업 아이콘 — ACTION_KO 의 모든 action 을 덮는다(빠지면 라벨 앞이 비어 보인다).
const ACTION_ICON = {
  upload:   '<i class="bi bi-upload action-upload"></i>',
  download: '<i class="bi bi-download action-download"></i>',
  delete:   '<i class="bi bi-trash action-delete"></i>',
  rename:   '<i class="bi bi-pencil action-rename"></i>',
  login:    '<i class="bi bi-box-arrow-in-right action-login"></i>',
  logout:   '<i class="bi bi-box-arrow-right action-logout"></i>',
  mkdir:    '<i class="bi bi-folder-plus action-mkdir"></i>',
  rmdir:    '<i class="bi bi-folder-minus action-rmdir"></i>',
  cwd_fail: '<i class="bi bi-folder-x action-cwd_fail"></i>',
  client_error: '<i class="bi bi-person-exclamation action-client_error"></i>',
};

let _logGroupMap = {};   // id → group object
let _pendingSearch = false;  // navToLogsFilters가 세팅, initLogsPage 완료 후 자동 검색
let _pendingCustomer = null; // 설정 > 고객 계정에서 '사용 내역'으로 넘어온 고객사
let _customerFilter = '';    // 그 고객사 조건 (드롭다운 없이 칩으로만 보인다)
let _selectedUsernames = []; // 계정 다중 선택 (정확 일치). 비어 있으면 전체

// 총건수 캐시: 필터(페이지/크기 제외)가 같으면 페이지 이동 시 재집계하지 않는다.
// 카운트는 목록과 병렬로 요청해 첫 페이지가 COUNT 를 기다리지 않게 한다.
let _logCountKey = null;     // 마지막으로 집계한 필터 문자열
let _logTotal = null;        // 정확한 총건수 (집계 중이면 null)

// Chrome 자동완성 차단: readonly 로 리셋 후 값 초기화 (포커스 시 해제는 index.html onfocus)
function _resetAutofillInputs() {
  ['logUserFilter', 'logIpFilter'].forEach(id => {
    const el = document.getElementById(id);
    el.setAttribute('readonly', '');
    el.value = '';
  });
}

async function initLogsPage() {
  // 왼쪽 탭으로 직접 들어온 경우엔 이전 검색 조건을 남기지 않는다.
  // 대시보드 드릴다운(navToLogsFilters)은 조건을 들고 오므로 건드리지 않는다.
  if (!_pendingSearch) _clearLogSearch();
  _resetAutofillInputs();

  const groups = await api('GET', '/groups');
  if (groups) {
    allGroups = groups;
    _logGroupMap = Object.fromEntries(groups.map(g => [String(g.id), g]));
    _renderGroupOptions();
  }
  _initGroupTooltip();
  _initLogColResize();

  if (_pendingCustomer !== null) {
    _setCustomerFilter(_pendingCustomer);
    _pendingCustomer = null;
    searchLogs(1);
    openUserPicker();       // 바로 '접근한 계정' 중에서 고르게 한다
    return;
  }
  if (_pendingSearch) {
    _pendingSearch = false;
    searchLogs(1);
  }
}

// 고객사 조건은 필터줄에 두지 않는다 — 설정 > 고객 계정의 '사용 내역'으로 넘어왔을 때만
// 칩으로 보여주고, 칩의 X 로 지운다.
function _setCustomerFilter(customer) {
  _customerFilter = customer || '';
  const chip = document.getElementById('logCustomerChip');
  if (!chip) return;
  chip.classList.toggle('d-none', !_customerFilter);
  if (_customerFilter) {
    document.getElementById('logCustomerChipName').textContent = _customerFilter;
  }
}

function clearCustomerFilter() {
  _setCustomerFilter('');
  _setSelectedUsernames([]);   // 고객사가 빠지면 그 안에서 고른 계정도 의미가 없다
  searchLogs(1);
}

// ── 접근 계정 다중 선택 ─────────────────────────────────────────────────────
function _setSelectedUsernames(list) {
  _selectedUsernames = list;
  const btn = document.getElementById('logUserPickBtn');
  const input = document.getElementById('logUserFilter');
  if (!btn || !input) return;
  btn.classList.toggle('btn-primary', list.length > 0);
  btn.classList.toggle('text-white', list.length > 0);
  btn.classList.toggle('btn-outline-secondary', list.length === 0);
  btn.title = list.length ? `선택된 계정 ${list.length}개 — ${list.join(', ')}` : '접근한 계정 중에서 여러 개 선택';
  if (list.length) {
    input.value = `${list.length}개 계정 선택`;
    input.setAttribute('readonly', '');
    input.classList.add('bg-body-secondary');
  } else if (input.value.endsWith('개 계정 선택')) {
    input.value = '';
    input.classList.remove('bg-body-secondary');
  }
}

async function openUserPicker() {
  const list = document.getElementById('userPickerList');
  list.innerHTML = '<div class="text-muted small py-2">불러오는 중…</div>';
  document.getElementById('userPickerSearch').value = '';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('userPickerModal')).show();
  // 계정 목록은 계정 조건을 뺀 나머지 필터(고객사·그룹·기간) 기준으로 뽑는다
  const params = _logParams({skipUsernames: true});
  try {
    const names = await api('GET', `/logs/usernames?${params}`);
    if (!names || !names.length) {
      list.innerHTML = '<div class="text-muted small py-2">이 조건에서 접근한 계정이 없습니다.</div>';
      _updateUserPickerCount();
      return;
    }
    list.innerHTML = names.map((n, i) => `
      <div class="form-check user-pick-row" data-name="${esc(n.toLowerCase())}">
        <input class="form-check-input" type="checkbox" id="upick${i}" value="${esc(n)}"
               ${_selectedUsernames.includes(n) ? 'checked' : ''} onchange="_updateUserPickerCount()">
        <label class="form-check-label small" for="upick${i}">${esc(n)}</label>
      </div>`).join('');
    _updateUserPickerCount();
  } catch (e) {
    list.innerHTML = `<div class="text-danger small py-2">${esc(e.message)}</div>`;
  }
}

function _pickerBoxes() {
  return Array.from(document.querySelectorAll('#userPickerList input[type=checkbox]'));
}

function _updateUserPickerCount() {
  const n = _pickerBoxes().filter(b => b.checked).length;
  document.getElementById('userPickerCount').textContent = n ? `${n}개 선택됨` : '선택 없음 = 전체';
}

function filterUserPicker() {
  const q = document.getElementById('userPickerSearch').value.trim().toLowerCase();
  document.querySelectorAll('#userPickerList .user-pick-row').forEach(row => {
    row.classList.toggle('d-none', q && !row.dataset.name.includes(q));
  });
}

function toggleAllUserPicker(on) {
  // 검색으로 걸러진 항목만 대상으로 한다 (보이는 것과 다르게 동작하면 헷갈린다)
  _pickerBoxes().forEach(b => {
    if (!b.closest('.user-pick-row').classList.contains('d-none')) b.checked = on;
  });
  _updateUserPickerCount();
}

function applyUserPicker() {
  _setSelectedUsernames(_pickerBoxes().filter(b => b.checked).map(b => b.value));
  bootstrap.Modal.getInstance(document.getElementById('userPickerModal'))?.hide();
  searchLogs(1);
}

// 설정 > 고객 계정에서 호출 — 그 고객사의 로그 조회로 이동해 계정 선택을 띄운다
function viewCustomerLogs(customer) {
  _pendingCustomer = customer || '';
  nav('logs');
}

// 옵션 title — 목록을 펼친 상태에서 항목에 마우스를 올리면 고객사/서비스가 보인다.
// (네이티브 select 는 option 에 커스텀 툴팁을 못 붙이므로 title 속성을 쓴다)
function _groupOptionTitle(g) {
  const rows = [];
  if (g.customer)    rows.push(`고객사: ${g.customer}`);
  if (g.application) rows.push(`서비스: ${g.application}`);
  if (g.description) rows.push(`설명: ${g.description}`);
  return rows.join('\n');
}

function _renderGroupOptions() {
  document.getElementById('logGroupFilter').innerHTML =
    '<option value="">전체 그룹</option>' +
    allGroups.map(g => {
      const tip = _groupOptionTitle(g);
      const prefix = g.telco ? esc(g.telco) + ' · ' : '';
      return `<option value="${g.id}"${tip ? ` title="${esc(tip)}"` : ''}>${prefix}${esc(g.name)}</option>`;
    }).join('');
}

function _initGroupTooltip() {
  const sel = document.getElementById('logGroupFilter');
  const tip = document.getElementById('logGroupTip');
  // initLogsPage 는 로그 탭에 들어올 때마다 불린다 — 가드가 없으면 같은 select 에
  // 핸들러가 계속 쌓인다 (_initLogColResize 와 같은 방식).
  if (!sel || sel.dataset.tipReady) return;
  sel.dataset.tipReady = '1';

  sel.addEventListener('mouseenter', () => {
    const g = _logGroupMap[sel.value];
    if (!g || (!g.application && !g.description && !g.customer)) { tip.classList.add('d-none'); return; }
    const rows = [];
    if (g.customer)    rows.push(`<span class="text-muted">고객사:</span> <b>${esc(g.customer)}</b>`);
    if (g.application) rows.push(`<span class="text-muted">서비스:</span> ${esc(g.application)}`);
    if (g.description) rows.push(`<span class="text-muted">설명:</span> ${esc(g.description)}`);
    tip.innerHTML = rows.join('<br>');
    tip.classList.remove('d-none');
  });
  sel.addEventListener('mouseleave', () => tip.classList.add('d-none'));
}

// ── 로그 표 열 너비 ────────────────────────────────────────────────────────
// 기본 폭은 index.html 의 colgroup 에 있고, 여기서는 사용자가 조절한 열만 덮어쓴다.
// 기본값을 바꾸면 VER 을 올려 이전에 저장된 폭을 무효화한다.
const LOG_COL_WIDTHS_VER = 'v5';   // 작업 열 확장(132 → 155px) + 저장 형식 변경(배열 → 인덱스맵)
const LOG_COL_MIN = 48;            // 이보다 좁아지면 머리글도 안 보인다
const LOG_COL_MAX = 700;
let _logColDefaults = [];          // colgroup 에 적힌 기본 폭 ('' = 남는 자리를 갖는 가변 열)

function _logCols() {
  const table = document.querySelector('#page-logs table');
  if (!table) return null;
  return {
    table,
    cols: Array.from(table.querySelectorAll('colgroup col')),
    ths:  Array.from(table.querySelectorAll('thead th')),
  };
}

function _loadLogColWidths() {
  if (localStorage.getItem('logColWidthsVer') !== LOG_COL_WIDTHS_VER) {
    localStorage.removeItem('logColWidths');   // 기본값이 바뀌었으면 예전 폭은 버린다
    return {};
  }
  try { return JSON.parse(localStorage.getItem('logColWidths')) || {}; }
  catch { return {}; }
}

function _saveLogColWidth(i, w) {
  const saved = _loadLogColWidths();
  saved[i] = Math.round(w);
  localStorage.setItem('logColWidths', JSON.stringify(saved));
  localStorage.setItem('logColWidthsVer', LOG_COL_WIDTHS_VER);
}

// 툴바의 '열 너비 초기화' 버튼
function resetLogColWidths() {
  const el = _logCols();
  if (!el) return;
  localStorage.removeItem('logColWidths');
  localStorage.removeItem('logColWidthsVer');
  el.cols.forEach((col, i) => { col.style.width = _logColDefaults[i] || ''; });
}

// 열 경계를 두 번 누르면 그 열을 지금 보이는 내용에 맞춘다.
// (줄바꿈되는 파일 경로 열은 Range 폭이 곧 열 폭이라 사실상 그대로 남는다)
function _autoFitLogCol(i) {
  const el = _logCols();
  if (!el) return;
  const th = el.ths[i];
  const cs = getComputedStyle(th);
  const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  const range = document.createRange();
  // 손잡이(.col-grip)는 th 밖으로 5px 삐져나온 절대배치 요소라 selectNodeContents 에 딸려 들어온다.
  // 마지막 자식이 손잡이면 범위에서 빼야 머리글의 실제 글자 폭이 나온다.
  const natural = node => {
    const last = node.lastElementChild;
    range.setStart(node, 0);
    range.setEnd(node, node.childNodes.length - (last && last.classList.contains('col-grip') ? 1 : 0));
    return range.getBoundingClientRect().width;
  };
  let need = natural(th);
  el.table.querySelectorAll('tbody tr').forEach(tr => {
    const td = tr.children[i];
    if (td && !td.hasAttribute('colspan')) need = Math.max(need, natural(td));
  });
  const w = Math.min(LOG_COL_MAX, Math.max(LOG_COL_MIN, Math.ceil(need + pad) + 4));
  el.cols[i].style.width = w + 'px';
  _saveLogColWidth(i, w);
}

// 끈 열만 바꾼다 — 남거나 모자란 폭은 가변 열(파일 경로)이 흡수하고,
// 그래도 넘치면 .table-responsive 가 가로로 스크롤한다.
// (예전에는 옆 열을 반대로 줄여 표 폭을 고정했는데, 그러면 파일 경로가 고정 폭이 되어
//  창을 넓혀도 더는 늘어나지 않았다.)
function _startLogColDrag(e, i) {
  const el = _logCols();
  if (!el) return;
  e.preventDefault();
  const startX = e.clientX;
  const startW = el.ths[i].getBoundingClientRect().width;
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
  let lastW = startW;

  const onMove = ev => {
    lastW = Math.min(LOG_COL_MAX, Math.max(LOG_COL_MIN, startW + ev.clientX - startX));
    el.cols[i].style.width = lastW + 'px';
  };
  const onUp = () => {
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    _saveLogColWidth(i, lastW);
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

function _initLogColResize() {
  const el = _logCols();
  // initLogsPage 는 탭에 들어올 때마다 불린다 — 가드가 없으면 손잡이가 계속 쌓인다.
  if (!el || el.table.dataset.resizeReady) return;
  el.table.dataset.resizeReady = '1';
  _logColDefaults = el.cols.map(c => c.style.width || '');

  const saved = _loadLogColWidths();
  Object.keys(saved).forEach(i => { if (el.cols[i]) el.cols[i].style.width = saved[i] + 'px'; });

  // 마지막 열은 오른쪽 경계가 없으므로 손잡이도 붙이지 않는다.
  el.ths.slice(0, -1).forEach((th, i) => {
    const grip = document.createElement('span');
    grip.className = 'col-grip';
    grip.title = '끌어서 너비 조절 · 두 번 누르면 내용에 맞춤';
    grip.addEventListener('mousedown', e => _startLogColDrag(e, i));
    grip.addEventListener('dblclick', e => { e.preventDefault(); _autoFitLogCol(i); });
    th.appendChild(grip);
  });
}

// skipUsernames: 계정 선택 목록을 뽑을 때는 계정 조건을 빼고 나머지 필터만 쓴다.
function _logParams({skipUsernames = false} = {}) {
  const params = new URLSearchParams();
  const grp = document.getElementById('logGroupFilter').value;
  const customer = _customerFilter;
  // 다중 선택 중이면 입력칸은 '3개 계정 선택' 같은 안내문이라 부분일치 조건으로 보내지 않는다
  const user = _selectedUsernames.length ? '' : document.getElementById('logUserFilter').value.trim();
  const ip = document.getElementById('logIpFilter').value.trim();
  const filePath = (document.getElementById('logFileFilter')?.value || '').trim();
  const action = document.getElementById('logActionFilter').value;
  const status = document.getElementById('logStatusFilter').value;
  const start = document.getElementById('logStartTime').value;
  const end = document.getElementById('logEndTime').value;
  if (grp) params.set('group_id', grp);
  if (customer) params.set('customer', customer);
  if (user) params.set('username', user);
  if (!skipUsernames && _selectedUsernames.length) params.set('usernames', _selectedUsernames.join(','));
  if (ip) params.set('client_ip', ip);
  if (filePath) params.set('file_path', filePath);
  if (action === '__exclude_login_logout__') params.set('exclude_actions', 'login,logout');
  else if (action === '__transfer_only__') params.set('exclude_actions', 'login,logout,cwd_fail,client_error,rename,mkdir,rmdir,delete');
  else if (action) params.set('action', action);
  if (status) params.set('status', status);
  // 날짜 단위 입력 — 시작일은 00:00:00, 종료일은 23:59:59.999 로 그 날 전체를 덮는다
  if (start) params.set('start_time', new Date(start + 'T00:00:00').toISOString());
  if (end) params.set('end_time', new Date(end + 'T23:59:59.999').toISOString());
  return params;
}

// 같은 내용이면 DOM 을 다시 쓰지 않는다 — 재작성 자체가 깜빡임이다.
function _setHtml(el, html) {
  if (el.innerHTML !== html) el.innerHTML = html;
}

// 총건수 집계가 이 시간 안에 끝나면 '집계 중' 단계를 건너뛰고 한 번만 그린다.
// (목록과 총건수를 병렬로 요청하므로, 예전에는 검색 한 번에 총건수·페이저가
//  '스피너 + 간이 페이저' → '건수 + 번호 페이저' 로 두 번 그려져 그 줄이 깜빡였다.)
const LOG_COUNT_PENDING_DELAY = 250;
let _logPendingTimer = null;

function _clearPendingTotal() {
  if (_logPendingTimer) { clearTimeout(_logPendingTimer); _logPendingTimer = null; }
}

// 총건수 표시 + 페이저 갱신. total=null 이면 집계 중.
function _renderLogTotal(itemCount) {
  const el = document.getElementById('logTotal');
  const pager = document.getElementById('logPager');
  if (_logTotal === null) {
    // 집계 중 표시는 조금 늦춘다. 대개 그 전에 총건수가 도착해 이 단계가 아예 안 보인다.
    _clearPendingTotal();
    _logPendingTimer = setTimeout(() => {
      _logPendingTimer = null;
      if (_logTotal !== null) return;         // 그 사이 도착
      const from = itemCount ? (logPage - 1) * logPageSize + 1 : 0;
      const to = itemCount ? from + itemCount - 1 : 0;
      _setHtml(el, itemCount
        ? `${from.toLocaleString()}–${to.toLocaleString()} / 총 <span class="text-muted">집계 중…</span>`
        : '결과 없음');
      // 총건수 미확정: 이전/다음만 제공 (이동할 마지막 페이지를 모르므로 직접 이동은 숨김)
      _renderPageJump(0);
      _setHtml(pager, itemCount ? _simplePager(itemCount < logPageSize) : '');
    }, LOG_COUNT_PENDING_DELAY);
    return;
  }
  _clearPendingTotal();
  const from = _logTotal ? (logPage - 1) * logPageSize + 1 : 0;
  const to = Math.min(logPage * logPageSize, _logTotal);
  _setHtml(el, _logTotal
    ? `${from.toLocaleString()}–${to.toLocaleString()} / 총 ${_logTotal.toLocaleString()}건` : '결과 없음');
  renderPager(_logTotal ? Math.ceil(_logTotal / logPageSize) : 0, logPage);
}

function _simplePager(isLast) {
  const go = n => `searchLogs(${n})`;
  return `<li class="page-item ${logPage===1?'disabled':''}"><a class="page-link" href="#" onclick="${go(logPage-1)}">‹</a></li>`
       + `<li class="page-item active"><span class="page-link">${logPage}</span></li>`
       + `<li class="page-item ${isLast?'disabled':''}"><a class="page-link" href="#" onclick="${go(logPage+1)}">›</a></li>`;
}

async function _countLogs(params, key, itemCount) {
  try {
    const c = await api('GET', `/logs/count?${params}`);
    if (!c || _logCountKey !== key) return;   // 그 사이 필터가 바뀌었으면 무시
    _logTotal = c.total;
    _renderLogTotal(itemCount);
  } catch (e) {
    if (_logCountKey !== key) return;
    document.getElementById('logTotal').textContent = `총건수 집계 실패: ${e.message}`;
  }
}

async function searchLogs(page) {
  const _fail = msg => {
    _clearPendingTotal();
    document.getElementById('logTable').innerHTML =
      `<tr><td colspan="9" class="text-center text-danger py-4">${msg}</td></tr>`;
    document.getElementById('logTotal').textContent = '';
    document.getElementById('logPager').innerHTML = '';
    _renderPageJump(0);
  };
  try {
  logPage = page || 1;
  logPageSize = parseInt(document.getElementById('logPageSize').value) || 50;
  const filterParams = _logParams();
  const key = filterParams.toString();
  const params = new URLSearchParams(filterParams);
  params.set('page', logPage);
  params.set('size', logPageSize);

  // 필터가 바뀐 경우에만 총건수를 새로 센다 (목록과 병렬 요청)
  const needCount = key !== _logCountKey;
  if (needCount) { _logCountKey = key; _logTotal = null; }

  const data = await api('GET', `/logs?${params}`);
  if (!data) return;

  const tbody = document.getElementById('logTable');
  if (!data.items.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">결과가 없습니다.</td></tr>';
    if (needCount && logPage === 1) { _logTotal = 0; }
    _renderLogTotal(0);
    if (needCount && logPage > 1) _countLogs(filterParams, key, 0);
    return;
  }
  tbody.innerHTML = data.items.map(l => {
    const d = new Date(l.log_time);
    const dt = `${d.getFullYear()}/${pad2(d.getMonth()+1)}/${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
    const action = l.action;
    // 매핑에 없으면 빈 문자열 — 예전에는 action 원문('cwd_fail')이 라벨 앞에 그대로 찍혀
    // 작업 열을 넘치게 만들었다.
    const icon = ACTION_ICON[action] || '';
    const filePath = l.file_path || '';
    const fileDisplay = action === 'rename'
      ? filePath.replace(' -> ', ' → ')
      : filePath;
    const hostname = l.device_hostname || '-';
    const deviceIp = l.device_ip || '-';
    const isTransfer = action === 'upload' || action === 'download' || action === 'delete';
    const sizeDisplay = isTransfer && l.file_size ? fmtBytes(l.file_size) : '-';
    const timeDisplay = l.transfer_time && l.transfer_time > 0 ? l.transfer_time.toFixed(1)+'s' : '-';
    return `<tr>
      <td class="small text-center text-nowrap">${dt}</td>
      <td class="small text-center text-truncate" style="overflow:hidden;font-family:monospace" title="${esc(hostname)}">${esc(deviceIp)}</td>
      <td class="small text-center text-truncate" style="overflow:hidden">${esc(l.username||'-')}</td>
      <td class="small text-center text-muted text-nowrap">${esc(l.client_ip||'-')}</td>
      <td class="text-center text-nowrap" title="${esc(ACTION_KO[action]||action)}">${icon} <span class="action-${action} small">${ACTION_KO[action]||esc(action)}</span></td>
      <td class="small" style="word-break:break-all;overflow:hidden" title="${esc(filePath)}">${esc(fileDisplay||'-')}</td>
      <td class="size-val small text-center text-nowrap">${sizeDisplay}</td>
      <td class="small text-center text-nowrap">${timeDisplay}</td>
      <td class="text-center"><span class="badge bg-${l.status==='success'?'success':'danger'}">${l.status==='success'?'성공':'실패'}</span></td>
    </tr>`;
  }).join('');

  // 표를 먼저 채운 뒤 총건수/페이저를 그린다 — 반대 순서면 행이 들어오면서 그 줄이 한 번 더 밀린다.
  _renderLogTotal(data.items.length);
  if (needCount) _countLogs(filterParams, key, data.items.length);
  } catch(e) {
    _fail(`조회 실패: ${e.message}`);
  }
}

// 페이지 직접 이동 입력 — 총 페이지 수가 확정됐을 때만 보인다.
let _logTotalPages = 0;

function _renderPageJump(total) {
  const box = document.getElementById('logPageJump');
  const input = document.getElementById('logPageJumpInput');
  if (!box || !input) return;
  _logTotalPages = total;
  box.classList.toggle('d-none', total <= 1);
  box.classList.toggle('d-flex', total > 1);
  if (total > 1) {
    input.max = total;
    input.placeholder = `1-${total}`;
  }
}

function jumpLogPage() {
  const input = document.getElementById('logPageJumpInput');
  const n = parseInt(input.value, 10);
  if (!_logTotalPages || !Number.isFinite(n)) return;
  input.value = '';
  searchLogs(Math.min(Math.max(1, n), _logTotalPages));
}

function renderPager(total, current) {
  const ul = document.getElementById('logPager');
  _renderPageJump(total);
  if (total <= 1) { _setHtml(ul, ''); return; }
  const go = n => `searchLogs(${n})`;
  let html = '';
  html += `<li class="page-item ${current===1?'disabled':''}"><a class="page-link" href="#" onclick="${go(1)}">«</a></li>`;
  html += `<li class="page-item ${current===1?'disabled':''}"><a class="page-link" href="#" onclick="${go(current-1)}">‹</a></li>`;
  const start = Math.max(1, current-2), end = Math.min(total, current+2);
  if (start > 1) html += `<li class="page-item disabled"><span class="page-link">…</span></li>`;
  for (let i=start; i<=end; i++) html += `<li class="page-item ${i===current?'active':''}"><a class="page-link" href="#" onclick="${go(i)}">${i}</a></li>`;
  if (end < total) html += `<li class="page-item disabled"><span class="page-link">…</span></li>`;
  html += `<li class="page-item ${current===total?'disabled':''}"><a class="page-link" href="#" onclick="${go(current+1)}">›</a></li>`;
  html += `<li class="page-item ${current===total?'disabled':''}"><a class="page-link" href="#" onclick="${go(total)}">»</a></li>`;
  _setHtml(ul, html);
}

async function _download(endpoint, ext) {
  const params = _logParams();
  const r = await fetch(`${API}${endpoint}?${params}`, {
    headers: {'Authorization': `Bearer ${token}`}
  });
  if (!r.ok) return alert('내보내기 실패');
  const blob = await r.blob();
  const a = document.createElement('a');
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = `ftp_logs_${new Date().toISOString().slice(0,10)}.${ext}`;
  a.click();
  // 안 풀어주면 내보낸 파일이 통째로 탭에 남는다 (대용량 CSV 를 여러 번 받으면 그만큼 쌓인다).
  // 다운로드가 시작될 때까지는 살려 둬야 하므로 다음 틱에 푼다.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function exportLogs() { await _download('/logs/export', 'csv'); }
async function exportXlsx() { await _download('/logs/export/xlsx', 'xlsx'); }

// 검색 조건만 기본값으로 되돌린다 (조회는 하지 않음).
function _clearLogFilters() {
  document.getElementById('logGroupFilter').value = '';
  _setCustomerFilter('');
  _setSelectedUsernames([]);
  _resetAutofillInputs();
  document.getElementById('logFileFilter').value = '';
  document.getElementById('logActionFilter').value = '__exclude_login_logout__';
  document.getElementById('logStatusFilter').value = '';
  document.getElementById('logStartTime').value = '';
  document.getElementById('logEndTime').value = '';
}

// 조건 + 결과를 화면 진입 직후 상태로 되돌린다 (탭 재진입용).
function _clearLogSearch() {
  _clearLogFilters();
  _clearPendingTotal();
  _logCountKey = null;
  _logTotal = null;
  document.getElementById('logTable').innerHTML =
    '<tr><td colspan="9" class="text-center text-muted py-4">검색 조건을 입력하고 조회하세요.</td></tr>';
  document.getElementById('logTotal').textContent = '';
  document.getElementById('logPager').innerHTML = '';
  _renderPageJump(0);
}

function resetLogFilters() {
  _clearLogFilters();
  searchLogs(1);
}

// 날짜 단위 조회라 '어제'는 어제 하루, 그 밖에는 N일 전부터 오늘까지가 된다.
function logDateQuick(dayOffset) {
  const start = new Date();
  const end = new Date();
  if (dayOffset === -1) {
    start.setDate(start.getDate() - 1);
    end.setDate(end.getDate() - 1);
  } else if (dayOffset < 0) {
    start.setDate(start.getDate() + dayOffset);
  }
  setLogDateRange(start, end);
  searchLogs(1);
}

// 로그 조회 기간 입력에 날짜를 넣는다 (대시보드 드릴다운도 이걸 쓴다 — 형식이 갈라지지 않게)
function setLogDateRange(start, end) {
  document.getElementById('logStartTime').value = start ? fmtLocalDate(start) : '';
  document.getElementById('logEndTime').value   = end ? fmtLocalDate(end) : '';
}

// 날짜 칸은 타이핑을 막아 두었으므로(onkeydown="return false") 어디를 눌러도 달력이 열리게 한다.
// readonly 로 막지 않는 이유: 브라우저에 따라 readonly 가 네이티브 달력까지 막아
// 날짜를 아예 못 고르는 상태가 된다. 실패해도 조용히 넘긴다 — 달력 아이콘은 그대로 동작한다.
function openDatePicker(el) {
  try { el.showPicker?.(); } catch { /* 사용자 제스처 밖에서 호출됨 — 무시 */ }
}

const ACTION_KO = {upload:'업로드', download:'다운로드', delete:'삭제', rename:'이름변경', login:'로그인', logout:'로그아웃', mkdir:'폴더생성', rmdir:'폴더삭제', cwd_fail:'디렉토리 이동 실패'};

let _logGroupMap = {};   // id → group object
let _pendingSearch = false;  // navToLogsFilters가 세팅, initLogsPage 완료 후 자동 검색

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
  _resetAutofillInputs();

  const groups = await api('GET', '/groups');
  if (groups) {
    allGroups = groups;
    _logGroupMap = Object.fromEntries(groups.map(g => [String(g.id), g]));

    // 텔코 목록 (그룹에서 추출, 중복 제거)
    const telcos = [...new Set(groups.map(g => g.telco).filter(Boolean))].sort();
    document.getElementById('logTelcoFilter').innerHTML =
      '<option value="">전체 텔코</option>' + telcos.map(t => `<option value="${t}">${t}</option>`).join('');

    _renderGroupOptions('');
  }
  _initGroupTooltip();
  _initLogColResize();

  if (_pendingSearch) {
    _pendingSearch = false;
    searchLogs(1);
  }
}

function _renderGroupOptions(telco) {
  const filtered = telco ? allGroups.filter(g => g.telco === telco) : allGroups;
  document.getElementById('logGroupFilter').innerHTML =
    '<option value="">전체 그룹</option>' +
    filtered.map(g => `<option value="${g.id}">${telco ? '' : (g.telco ? g.telco + ' · ' : '')}${g.name}</option>`).join('');
}

function onTelcoFilter() {
  const telco = document.getElementById('logTelcoFilter').value;
  _renderGroupOptions(telco);
  searchLogs(1);
}

function _initGroupTooltip() {
  const sel = document.getElementById('logGroupFilter');
  const tip = document.getElementById('logGroupTip');

  sel.addEventListener('mouseenter', () => {
    const g = _logGroupMap[sel.value];
    if (!g || (!g.application && !g.description && !g.customer)) { tip.classList.add('d-none'); return; }
    const rows = [];
    if (g.application) rows.push(`<span class="text-muted">서비스:</span> <b>${esc(g.application)}</b>`);
    if (g.customer)    rows.push(`<span class="text-muted">고객사:</span> ${esc(g.customer)}`);
    if (g.description) rows.push(`<span class="text-muted">설명:</span> ${esc(g.description)}`);
    tip.innerHTML = rows.join('<br>');
    tip.classList.remove('d-none');
  });
  sel.addEventListener('mouseleave', () => tip.classList.add('d-none'));
}

function _initLogColResize() {
  const table = document.querySelector('#page-logs table');
  if (!table || table.dataset.resizeReady) return;
  table.dataset.resizeReady = '1';

  const cols = Array.from(table.querySelectorAll('colgroup col'));
  const ths  = Array.from(table.querySelectorAll('thead th'));
  const row  = table.querySelector('thead tr');

  // 저장된 폭 복원 (v2: 컬럼 기본값 변경 시 이전 저장값 무효화)
  const WIDTHS_VER = 'v3';
  const savedRaw = localStorage.getItem('logColWidths');
  const savedMeta = localStorage.getItem('logColWidthsVer');
  const saved = (savedMeta === WIDTHS_VER && savedRaw) ? JSON.parse(savedRaw) : null;
  if (!saved) localStorage.removeItem('logColWidths');
  if (saved) cols.forEach((col, i) => { if (saved[i]) col.style.width = saved[i] + 'px'; });

  const ZONE = 6; // 각 th 우측 경계에서 ±px 이내를 드래그 존으로 인식

  function hitCol(clientX) {
    // 마지막 컬럼 경계는 제외 (last-child는 경계 없음)
    for (let i = 0; i < ths.length - 1; i++) {
      if (Math.abs(clientX - ths[i].getBoundingClientRect().right) <= ZONE) return i;
    }
    return -1;
  }

  row.addEventListener('mousemove', e => {
    row.style.cursor = hitCol(e.clientX) >= 0 ? 'col-resize' : '';
  });
  row.addEventListener('mouseleave', () => { row.style.cursor = ''; });

  row.addEventListener('mousedown', e => {
    const i = hitCol(e.clientX);
    if (i < 0) return;
    e.preventDefault();
    const startX    = e.clientX;
    const startW    = ths[i].getBoundingClientRect().width;
    const nextStartW = i + 1 < ths.length ? ths[i + 1].getBoundingClientRect().width : 0;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = e => {
      const delta  = e.clientX - startX;
      const w      = Math.max(40, startW + delta);
      const actual = w - startW; // min 클램핑 후 실제 변화량
      cols[i].style.width = w + 'px';
      // 인접 컬럼을 반대 방향으로 조정 → 테이블 전체 폭 유지
      if (cols[i + 1] && nextStartW > 0) {
        cols[i + 1].style.width = Math.max(40, nextStartW - actual) + 'px';
      }
    };
    const onUp = () => {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      localStorage.setItem('logColWidths',
        JSON.stringify(ths.map(t => Math.round(t.getBoundingClientRect().width))));
      localStorage.setItem('logColWidthsVer', WIDTHS_VER);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

function _logParams() {
  const params = new URLSearchParams();
  const grp = document.getElementById('logGroupFilter').value;
  const user = document.getElementById('logUserFilter').value.trim();
  const ip = document.getElementById('logIpFilter').value.trim();
  const filePath = (document.getElementById('logFileFilter')?.value || '').trim();
  const action = document.getElementById('logActionFilter').value;
  const status = document.getElementById('logStatusFilter').value;
  const start = document.getElementById('logStartTime').value;
  const end = document.getElementById('logEndTime').value;
  if (grp) params.set('group_id', grp);
  if (user) params.set('username', user);
  if (ip) params.set('client_ip', ip);
  if (filePath) params.set('file_path', filePath);
  if (action === '__exclude_login_logout__') params.set('exclude_actions', 'login,logout');
  else if (action === '__transfer_only__') params.set('exclude_actions', 'login,logout,cwd_fail,rename,mkdir,rmdir,delete');
  else if (action) params.set('action', action);
  if (status) params.set('status', status);
  if (start) params.set('start_time', new Date(start).toISOString());
  if (end) params.set('end_time', new Date(end).toISOString());
  return params;
}

// 총건수 표시 + 페이저 갱신. total=null 이면 집계 중.
function _renderLogTotal(itemCount) {
  const el = document.getElementById('logTotal');
  const pager = document.getElementById('logPager');
  if (_logTotal === null) {
    const from = itemCount ? (logPage - 1) * logPageSize + 1 : 0;
    const to = itemCount ? from + itemCount - 1 : 0;
    el.innerHTML = itemCount
      ? `${from.toLocaleString()}–${to.toLocaleString()} / 총 <span class="text-muted"><span class="spinner-border spinner-border-sm" style="width:.7rem;height:.7rem"></span> 집계 중</span>`
      : '결과 없음';
    // 총건수 미확정: 이전/다음만 제공
    pager.innerHTML = itemCount ? _simplePager(itemCount < logPageSize) : '';
    return;
  }
  const from = _logTotal ? (logPage - 1) * logPageSize + 1 : 0;
  const to = Math.min(logPage * logPageSize, _logTotal);
  el.textContent = _logTotal
    ? `${from.toLocaleString()}–${to.toLocaleString()} / 총 ${_logTotal.toLocaleString()}건` : '결과 없음';
  renderPager(Math.ceil(_logTotal / logPageSize), logPage);
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
    document.getElementById('logTable').innerHTML =
      `<tr><td colspan="9" class="text-center text-danger py-4">${msg}</td></tr>`;
    document.getElementById('logTotal').textContent = '';
    document.getElementById('logPager').innerHTML = '';
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
  _renderLogTotal(data.items.length);
  if (needCount) _countLogs(filterParams, key, data.items.length);

  tbody.innerHTML = data.items.map(l => {
    const d = new Date(l.log_time);
    const dt = `${d.getFullYear()}/${pad2(d.getMonth()+1)}/${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
    const action = l.action;
    const icon = {upload:'<i class="bi bi-upload action-upload"></i>', download:'<i class="bi bi-download action-download"></i>', delete:'<i class="bi bi-trash action-delete"></i>', rename:'<i class="bi bi-pencil action-rename"></i>', login:'<i class="bi bi-box-arrow-in-right action-login"></i>', logout:'<i class="bi bi-box-arrow-right action-logout"></i>'}[action] || action;
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
      <td class="text-center text-nowrap">${icon} <span class="action-${action} small">${ACTION_KO[action]||esc(action)}</span></td>
      <td class="small" style="word-break:break-all;overflow:hidden" title="${esc(filePath)}">${esc(fileDisplay||'-')}</td>
      <td class="size-val small text-center text-nowrap">${sizeDisplay}</td>
      <td class="small text-center text-nowrap">${timeDisplay}</td>
      <td class="text-center"><span class="badge bg-${l.status==='success'?'success':'danger'}">${l.status==='success'?'성공':'실패'}</span></td>
    </tr>`;
  }).join('');
  } catch(e) {
    _fail(`조회 실패: ${e.message}`);
  }
}

function renderPager(total, current) {
  const ul = document.getElementById('logPager');
  if (total <= 1) { ul.innerHTML=''; return; }
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
  ul.innerHTML = html;
}

async function _download(endpoint, ext) {
  const params = _logParams();
  const r = await fetch(`${API}${endpoint}?${params}`, {
    headers: {'Authorization': `Bearer ${token}`}
  });
  if (!r.ok) return alert('내보내기 실패');
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `ftp_logs_${new Date().toISOString().slice(0,10)}.${ext}`;
  a.click();
}

async function exportLogs() { await _download('/logs/export', 'csv'); }
async function exportXlsx() { await _download('/logs/export/xlsx', 'xlsx'); }

function resetLogFilters() {
  document.getElementById('logTelcoFilter').value = '';
  _renderGroupOptions('');
  document.getElementById('logGroupFilter').value = '';
  _resetAutofillInputs();
  document.getElementById('logFileFilter').value = '';
  document.getElementById('logActionFilter').value = '__exclude_login_logout__';
  document.getElementById('logStatusFilter').value = '';
  document.getElementById('logStartTime').value = '';
  document.getElementById('logEndTime').value = '';
  searchLogs(1);
}

function logDateQuick(dayOffset) {
  const now = new Date();
  let start, end;
  if (dayOffset === -1) {
    // 어제 전체
    start = new Date(now); start.setDate(start.getDate()-1); start.setHours(0,0,0,0);
    end   = new Date(now); end.setDate(end.getDate()-1);     end.setHours(23,59,59,999);
  } else if (dayOffset === 0) {
    // 오늘 00:00 ~ 지금
    start = new Date(now); start.setHours(0,0,0,0);
    end   = now;
  } else {
    // N일 전 00:00 ~ 지금
    start = new Date(now); start.setDate(start.getDate()+dayOffset); start.setHours(0,0,0,0);
    end   = now;
  }
  document.getElementById('logStartTime').value = fmtLocalInput(start);
  document.getElementById('logEndTime').value   = fmtLocalInput(end);
  searchLogs(1);
}

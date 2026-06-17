const ACTION_KO = {upload:'업로드', download:'다운로드', delete:'삭제', rename:'이름변경', login:'로그인', logout:'로그아웃', mkdir:'폴더생성', rmdir:'폴더삭제'};

async function initLogsPage() {
  const [devices, groups] = await Promise.all([api('GET', '/devices'), api('GET', '/groups')]);
  if (devices) {
    document.getElementById('logDeviceFilter').innerHTML =
      '<option value="">전체 장비</option>' + devices.map(d=>`<option value="${d.id}">${d.hostname}</option>`).join('');
  }
  if (groups) {
    document.getElementById('logGroupFilter').innerHTML =
      '<option value="">전체 그룹</option>' + groups.map(g=>`<option value="${g.id}">${g.telco?g.telco+' · ':''}${g.name}</option>`).join('');
  }
}

function _logParams() {
  const params = new URLSearchParams();
  const grp = document.getElementById('logGroupFilter').value;
  const dev = document.getElementById('logDeviceFilter').value;
  const user = document.getElementById('logUserFilter').value.trim();
  const ip = document.getElementById('logIpFilter').value.trim();
  const action = document.getElementById('logActionFilter').value;
  const start = document.getElementById('logStartTime').value;
  const end = document.getElementById('logEndTime').value;
  if (grp) params.set('group_id', grp);
  if (dev) params.set('device_id', dev);
  if (user) params.set('username', user);
  if (ip) params.set('client_ip', ip);
  if (action) params.set('action', action);
  if (start) params.set('start_time', new Date(start).toISOString());
  if (end) params.set('end_time', new Date(end).toISOString());
  return params;
}

async function searchLogs(page) {
  logPage = page || 1;
  logPageSize = parseInt(document.getElementById('logPageSize').value) || 50;
  const params = _logParams();
  params.set('page', logPage);
  params.set('size', logPageSize);

  const data = await api('GET', `/logs?${params}`);
  if (!data) return;

  const from = data.total ? (logPage - 1) * logPageSize + 1 : 0;
  const to = Math.min(logPage * logPageSize, data.total);
  document.getElementById('logTotal').textContent =
    data.total ? `${from.toLocaleString()}–${to.toLocaleString()} / 총 ${data.total.toLocaleString()}건` : '결과 없음';

  const tbody = document.getElementById('logTable');
  if (!data.items.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">결과가 없습니다.</td></tr>';
    document.getElementById('logPager').innerHTML = '';
    return;
  }

  tbody.innerHTML = data.items.map(l => {
    const d = new Date(l.log_time);
    const dt = `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
    const action = l.action;
    const icon = {upload:'<i class="bi bi-upload action-upload"></i>', download:'<i class="bi bi-download action-download"></i>', delete:'<i class="bi bi-trash action-delete"></i>', rename:'<i class="bi bi-pencil action-rename"></i>', login:'<i class="bi bi-box-arrow-in-right action-login"></i>', logout:'<i class="bi bi-box-arrow-right action-logout"></i>'}[action] || action;
    const filePath = l.file_path || '';
    const fileDisplay = action === 'rename'
      ? filePath.replace(' -> ', '\n→ ')
      : filePath;
    const hostname = l.device_hostname || '-';
    return `<tr>
      <td class="small text-nowrap">${dt}</td>
      <td class="small text-truncate" style="overflow:hidden" title="${hostname}">${hostname}</td>
      <td class="small text-truncate" style="overflow:hidden">${l.username||'-'}</td>
      <td class="small text-muted text-nowrap">${l.client_ip||'-'}</td>
      <td class="text-nowrap">${icon} <span class="action-${action} small">${ACTION_KO[action]||action}</span></td>
      <td class="small" style="word-break:break-all;white-space:pre-wrap;overflow:hidden" title="${filePath.replace(/"/g,'&quot;')}">${fileDisplay||'-'}</td>
      <td class="size-val small text-nowrap">${fmtBytes(l.file_size)}</td>
      <td class="small text-nowrap">${l.transfer_time ? l.transfer_time.toFixed(1)+'s' : '-'}</td>
      <td><span class="badge bg-${l.status==='success'?'success':'danger'}">${l.status==='success'?'성공':'실패'}</span></td>
    </tr>`;
  }).join('');

  renderPager(Math.ceil(data.total / logPageSize), logPage);
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

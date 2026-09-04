function settingsMsg(id, type, text) {
  const el = document.getElementById(id);
  el.className = `alert alert-${type} py-2 small`;
  el.textContent = text;
}

function renderVersion(v) {
  if (!v) return;
  document.getElementById('verBranch').textContent = v.branch || '-';
  document.getElementById('verCommit').textContent = v.commit || '-';
  document.getElementById('verDate').textContent = v.commit_date || '-';
  document.getElementById('verSubject').textContent = v.subject || '-';
  const st = document.getElementById('verStatus');
  if (v.error) {
    st.innerHTML = `<span class="text-danger">원격 확인 실패: ${esc(v.error)}</span>`;
  } else if (!v.checked) {
    st.innerHTML = '<span class="text-muted">미확인 — "업데이트 확인"을 눌러주세요</span>';
  } else if (v.update_available) {
    st.innerHTML = `<span class="text-warning fw-semibold">업데이트 가능 (원격이 ${v.behind} 커밋 앞섬)</span>`;
  } else {
    st.innerHTML = '<span class="text-success">최신 상태</span>';
  }
}

async function loadVersion() {
  const updMsg = document.getElementById('updMsg');
  updMsg.className = 'alert d-none py-2 small';
  updMsg.textContent = '';
  const runBtn = document.getElementById('runUpdBtn');
  if (runBtn) runBtn.disabled = false;
  try {
    renderVersion(await api('GET', '/settings/version'));
  } catch (e) { settingsMsg('updMsg', 'danger', e.message); }
}

let _storage = null;   // 마지막 조회 결과 — '빈 파티션 숨기기' 토글이 다시 그릴 때 쓴다

// 파티션의 데이터 기간 표시. 미리 만들어 둔 빈 파티션과 실제 데이터를 구분한다.
function _partRange(p) {
  if (!p.has_rows) return '<span class="text-muted">비어 있음</span>';
  const f = new Date(p.first_log), t = new Date(p.last_log);
  const d = x => `${x.getFullYear()}/${pad2(x.getMonth()+1)}/${pad2(x.getDate())}`;
  return `${d(f)} ~ ${d(t)}`;
}

function renderStorageTable() {
  const tbody = document.getElementById('storageTable');
  const s = _storage;
  if (!s) return;
  const hideEmpty = document.getElementById('stHideEmpty').checked;
  const rows = s.partitions.filter(p => !hideEmpty || p.has_rows || p.name === 'ftp_logs_default');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-3">${
      s.partitions.length ? '데이터가 있는 파티션이 없습니다.' : '파티션이 없습니다.'}</td></tr>`;
    return;
  }
  // 당월 이후 파티션은 수집 중이므로 삭제 버튼을 주지 않는다 (서버도 같은 기준으로 막는다)
  const now = new Date();
  const curName = `ftp_logs_${now.getFullYear()}_${pad2(now.getMonth() + 1)}`;
  tbody.innerHTML = rows.map(p => {
    const monthly = /^ftp_logs_\d{4}_\d{2}$/.test(p.name);
    const deletable = monthly && p.name < curName;
    return `
      <tr class="${p.name === 'ftp_logs_default' && s.default_rows > 0 ? 'table-warning' : ''}">
        <td class="font-monospace">${esc(p.name)}</td>
        <td>${_partRange(p)}</td>
        <td class="text-end">${p.rows_est.toLocaleString()}</td>
        <td class="text-end">${fmtBytes(p.table_bytes)}</td>
        <td class="text-end">${fmtBytes(p.index_bytes)}</td>
        <td class="text-end fw-semibold">${fmtBytes(p.total_bytes)}</td>
        <td class="text-end">${deletable
          ? `<button class="btn btn-xs btn-outline-danger" onclick="dropPartition('${esc(p.name)}')" title="이 달 데이터를 완전히 삭제"><i class="bi bi-trash"></i></button>`
          : '<span class="text-muted">-</span>'}</td>
      </tr>`;
  }).join('');
}

function _renderStorage(s) {
  _storage = s;
  document.getElementById('stDbSize').textContent = fmtBytes(s.db_bytes);
  document.getElementById('stLogsSize').textContent = fmtBytes(s.ftp_logs_bytes);
  document.getElementById('stRetentionInput').value = s.retention_months;

  // 실제 데이터가 있는 파티션만 모아 보유 기간을 계산 (미리 만든 빈 파티션은 제외)
  const filled = s.partitions.filter(p => p.has_rows);
  const rangeEl = document.getElementById('stDataRange');
  if (filled.length) {
    const first = filled.map(p => p.first_log).sort()[0];
    const last  = filled.map(p => p.last_log).sort().slice(-1)[0];
    const d = x => { const v = new Date(x); return `${v.getFullYear()}/${pad2(v.getMonth()+1)}/${pad2(v.getDate())}`; };
    rangeEl.textContent = `${d(first)} ~ ${d(last)} (${filled.length}개 파티션)`;
  } else {
    rangeEl.innerHTML = '<span class="text-muted">데이터 없음</span>';
  }

  // 켜짐/꺼짐을 스위치가 아니라 선택 버튼으로 — 되돌릴 수 없는 동작이라 상태가 분명해야 한다
  document.getElementById('stAutoPurgeOn').checked = !!s.autopurge_enabled;
  document.getElementById('stAutoPurgeOff').checked = !s.autopurge_enabled;
  document.getElementById('stAutoPurgePct').value = s.autopurge_percent;

  const disk = document.getElementById('stDisk');
  if (s.disk_total_bytes) {
    const cls = s.disk_percent >= 90 ? 'text-danger fw-semibold'
              : s.disk_percent >= 80 ? 'text-warning-emphasis fw-semibold' : '';
    disk.innerHTML = `<span class="${cls}">${s.disk_percent}%</span>`
      + ` <span class="text-muted">(${fmtBytes(s.disk_used_bytes)} / ${fmtBytes(s.disk_total_bytes)})</span>`;
  } else {
    disk.innerHTML = '<span class="text-muted">확인 불가</span>';
  }

  const def = document.getElementById('stDefault');
  if (s.default_rows > 0) {
    def.innerHTML = `<span class="text-danger fw-semibold">${s.default_rows.toLocaleString()}행 잔존</span>`
      + ` <span class="text-muted">(${s.default_months.map(esc).join(', ')})</span>`;
    settingsMsg('storageMsg', 'warning', 'default 파티션에 데이터가 남아 있습니다. 재배치가 필요합니다.');
  } else {
    def.innerHTML = '<span class="text-success">비어 있음</span>';
  }
  renderStorageTable();
}

async function loadStorage() {
  const msg = document.getElementById('storageMsg');
  msg.className = 'alert d-none py-2 small';
  try {
    const s = await api('GET', '/settings/storage');
    if (s) _renderStorage(s);
  } catch (e) {
    settingsMsg('storageMsg', 'danger', e.message);
    document.getElementById('storageTable').innerHTML = '';
  }
}

async function saveRetention() {
  const months = parseInt(document.getElementById('stRetentionInput').value, 10);
  if (!Number.isFinite(months) || months < 1 || months > 120) {
    settingsMsg('storageMsg', 'danger', '보존 기간은 1~120개월 사이여야 합니다.');
    return;
  }
  try {
    const s = await api('PUT', '/settings/storage/retention', {months});
    if (s) _renderStorage(s);
    settingsMsg('storageMsg', 'success', `보존 기간을 ${months}개월로 저장했습니다.`);
  } catch (e) { settingsMsg('storageMsg', 'danger', e.message); }
}

async function saveAutoPurge() {
  const enabled = document.getElementById('stAutoPurgeOn').checked;
  const percent = parseInt(document.getElementById('stAutoPurgePct').value, 10);
  if (!Number.isFinite(percent) || percent < 50 || percent > 99) {
    settingsMsg('storageMsg', 'danger', '임계치는 50~99% 사이여야 합니다.');
    return;
  }
  if (enabled && !confirm(
      `디스크 ${percent}% 초과 시 가장 오래된 월 파티션부터 자동 삭제합니다.\n\n` +
      '백업 파일이 없어도 삭제되며 되돌릴 수 없습니다. 계속할까요?')) return;
  try {
    const s = await api('PUT', '/settings/storage/autopurge', {enabled, percent});
    if (s) _renderStorage(s);
    settingsMsg('storageMsg', 'success',
      enabled ? `자동 정리를 켰습니다 (임계치 ${percent}%).` : '자동 정리를 껐습니다.');
  } catch (e) { settingsMsg('storageMsg', 'danger', e.message); }
}

async function dropPartition(name) {
  const p = _storage?.partitions.find(x => x.name === name);
  const size = p ? ` (${fmtBytes(p.total_bytes)}, 약 ${p.rows_est.toLocaleString()}행)` : '';
  if (!confirm(`${name}${size} 을(를) 삭제합니다.\n\n되돌릴 수 없습니다. 백업 파일이 있는지 확인하셨습니까?`)) return;
  try {
    const s = await api('DELETE', `/settings/storage/partitions/${encodeURIComponent(name)}`);
    if (s) _renderStorage(s);
    settingsMsg('storageMsg', 'success', `${name} 파티션을 삭제했습니다.`);
  } catch (e) { settingsMsg('storageMsg', 'danger', e.message); }
}

async function getTelcos() {
  allTelcos = (await api('GET', '/telcos')) || [];
  return allTelcos;
}

async function loadTelcos() {
  const list = document.getElementById('telcoList');
  try {
    const telcos = await getTelcos();
    list.innerHTML = telcos.length
      ? telcos.map(t => `
          <li class="list-group-item d-flex align-items-center px-0">
            <span>${esc(t.name)}</span>
            <button class="btn btn-xs btn-outline-danger ms-auto" onclick="deleteTelco(${t.id})"><i class="bi bi-trash"></i></button>
          </li>`).join('')
      : '<li class="list-group-item text-muted small px-0">등록된 통신사가 없습니다.</li>';
  } catch (e) { settingsMsg('telcoMsg', 'danger', e.message); }
}

async function addTelco() {
  const input = document.getElementById('telcoName');
  const name = input.value.trim();
  if (!name) return;
  try {
    await api('POST', '/telcos', {name});
    input.value = '';
    settingsMsg('telcoMsg', 'success', `'${name}' 추가됨`);
    loadTelcos();
  } catch (e) { settingsMsg('telcoMsg', 'danger', e.message); }
}

async function deleteTelco(id) {
  if (!confirm('이 통신사를 삭제하시겠습니까?')) return;
  try {
    await api('DELETE', `/telcos/${id}`);
    loadTelcos();
  } catch (e) { settingsMsg('telcoMsg', 'danger', e.message); }
}

async function changePassword() {
  const cur = document.getElementById('pwCurrent').value;
  const nw = document.getElementById('pwNew').value;
  const cf = document.getElementById('pwConfirm').value;
  if (nw.length < 8) { settingsMsg('pwMsg', 'danger', '새 비밀번호는 8자 이상이어야 합니다.'); return; }
  if (nw !== cf) { settingsMsg('pwMsg', 'danger', '새 비밀번호 확인이 일치하지 않습니다.'); return; }
  try {
    await api('POST', '/settings/password', {current_password: cur, new_password: nw});
    settingsMsg('pwMsg', 'success', '비밀번호가 변경되었습니다.');
    document.getElementById('pwCurrent').value = '';
    document.getElementById('pwNew').value = '';
    document.getElementById('pwConfirm').value = '';
  } catch (e) { settingsMsg('pwMsg', 'danger', e.message); }
}

async function checkUpdate() {
  const btn = document.getElementById('checkUpdBtn');
  btn.disabled = true; btn.textContent = '확인 중...';
  try {
    const v = await api('POST', '/settings/check-update');
    renderVersion(v);
  } catch (e) { settingsMsg('updMsg', 'danger', e.message); }
  finally { btn.disabled = false; btn.textContent = '업데이트 확인'; }
}

async function runUpdate() {
  if (!confirm('지금 업데이트하시겠습니까?\norigin/main을 받아 재배포하며 서비스가 1~2분간 재시작됩니다.')) return;
  const btn = document.getElementById('runUpdBtn');
  btn.disabled = true;
  try {
    const r = await api('POST', '/settings/update');
    settingsMsg('updMsg', 'success', (r && r.message) || '업데이트를 시작했습니다.');
    _pollRestart();
  } catch (e) {
    settingsMsg('updMsg', 'danger', e.message);
    btn.disabled = false;
  }
}

function _pollRestart() {
  let attempts = 0;
  const MAX = 40;
  settingsMsg('updMsg', 'warning', '재시작 대기 중... (최대 2분)');
  const timer = setInterval(async () => {
    attempts++;
    try {
      await api('GET', '/settings/version');
      clearInterval(timer);
      settingsMsg('updMsg', 'success', '재시작이 완료되었습니다. 버전 정보를 갱신합니다.');
      loadVersion();
      document.getElementById('runUpdBtn').disabled = false;
    } catch (_) {
      if (attempts >= MAX) {
        clearInterval(timer);
        settingsMsg('updMsg', 'danger', '재시작 대기 시간이 초과되었습니다. 페이지를 새로고침하세요.');
        document.getElementById('runUpdBtn').disabled = false;
      }
    }
  }, 3000);
}

function _applyMuteState(muted) {
  document.getElementById('notifyMuteToggle').checked = muted;
  const label = document.getElementById('notifyMuteLabel');
  if (muted) {
    label.textContent = '알림 중지됨';
    label.className = 'small text-danger fw-semibold';
  } else {
    label.textContent = '발송 중';
    label.className = 'small text-muted';
  }
}

async function loadNotify() {
  const [d, m] = await Promise.all([
    api('GET', '/settings/notify'),
    api('GET', '/settings/notify/mute'),
  ]);
  if (!d) return;
  document.getElementById('notifyWebhookUrl').value = d.webhook_url || '';
  document.getElementById('notifyHmsUrl').value     = d.hms_url || '';
  if (m) _applyMuteState(m.muted);
  loadAlerts();
}

// ── 이상 감지 임계값 ────────────────────────────────────────────────────────
// 입력 id ↔ API 필드. 저장 시 같은 표를 역방향으로 사용한다.
const ALERT_FIELDS = {
  alMadK: 'mad_k',
  alThroughputDrop: 'throughput_drop',
  alThroughputSlowPct: 'throughput_slow_pct',
  alFailFloor: 'fail_rate_floor',
  alLoginFailFloor: 'login_fail_rate_floor',
  alCwdFloor: 'cwd_fail_floor',
  alMinSamples: 'min_samples',
  alMinLoginSamples: 'min_login_samples',
  alMinCwdSamples: 'min_cwd_samples',
  alMinLargeSamples: 'min_large_samples',
};
// 숫자가 아니라 따로 다루는 필드 (ALERT_FIELDS 는 parseFloat 로 읽는다)
const ALERT_CWD_IGNORE = 'alCwdIgnorePaths';

function _renderAlerts(a) {
  if (!a) return;
  for (const [id, key] of Object.entries(ALERT_FIELDS)) {
    document.getElementById(id).value = a[key];
  }
  document.getElementById(ALERT_CWD_IGNORE).value = a.cwd_ignore_paths || '';
  document.getElementById('alLargeBytes').textContent = fmtBytes(a.large_file_bytes) + ' 이상';
  document.getElementById('alBucketInfo').textContent =
    `${a.bucket_minutes}분 버킷 · 최근 ${a.baseline_days}일 기준`;
}

async function loadAlerts() {
  const msg = document.getElementById('alertMsg');
  msg.className = 'alert d-none py-2 small';
  try {
    _renderAlerts(await api('GET', '/settings/alerts'));
  } catch (e) { settingsMsg('alertMsg', 'danger', e.message); }
}

async function saveAlerts() {
  const body = {};
  for (const [id, key] of Object.entries(ALERT_FIELDS)) {
    const v = parseFloat(document.getElementById(id).value);
    if (Number.isNaN(v)) { settingsMsg('alertMsg', 'danger', '빈 값이나 숫자가 아닌 값이 있습니다.'); return; }
    body[key] = v;
  }
  body.cwd_ignore_paths = document.getElementById(ALERT_CWD_IGNORE).value.trim();
  try {
    _renderAlerts(await api('PUT', '/settings/alerts', body));
    settingsMsg('alertMsg', 'success', '저장했습니다. 다음 판정 주기(약 5분)부터 적용됩니다.');
  } catch (e) { settingsMsg('alertMsg', 'danger', e.message); }
}

async function resetAlerts() {
  if (!confirm('임계값을 기본값으로 되돌릴까요?')) return;
  try {
    _renderAlerts(await api('POST', '/settings/alerts/reset'));
    settingsMsg('alertMsg', 'success', '기본값으로 되돌렸습니다.');
  } catch (e) { settingsMsg('alertMsg', 'danger', e.message); }
}

async function toggleMute(muted) {
  const msg = document.getElementById('notifyMsg');
  try {
    await api('POST', `/settings/notify/mute?muted=${muted}`);
    _applyMuteState(muted);
    msg.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>알림 ${muted ? '중지' : '재개'}됨</span>`;
  } catch(e) {
    document.getElementById('notifyMuteToggle').checked = !muted;
    msg.innerHTML = `<span class="text-danger">변경 실패: ${esc(e.message)}</span>`;
  }
  setTimeout(() => { msg.innerHTML = ''; }, 3000);
}

async function saveNotify() {
  const body = {
    webhook_url: document.getElementById('notifyWebhookUrl').value.trim(),
    hms_url:     document.getElementById('notifyHmsUrl').value.trim(),
  };
  const msg = document.getElementById('notifyMsg');
  try {
    await api('PUT', '/settings/notify', body);
    msg.innerHTML = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>저장되었습니다.</span>';
  } catch(e) {
    msg.innerHTML = `<span class="text-danger">저장 실패: ${esc(e.message)}</span>`;
  }
  setTimeout(() => { msg.innerHTML = ''; }, 3000);
}

async function testNotify(channel = 'all') {
  const label = {webhook:'웹훅', hms:'HMS', all:'전체'}[channel] || channel;
  const msg = document.getElementById('notifyMsg');
  msg.innerHTML = `<span class="text-muted">${label} 테스트 발송 중...</span>`;
  try {
    await api('POST', `/settings/notify/test?channel=${channel}`);
    msg.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>${label} 테스트 발송 성공</span>`;
  } catch(e) {
    msg.innerHTML = `<span class="text-danger">발송 실패: ${esc(e.message)}</span>`;
  }
  setTimeout(() => { msg.innerHTML = ''; }, 4000);
}

// ── 계정 보안 탭 ──────────────────────────────────────────────────────────────

async function loadSecurity() {
  try {
    const d = await api('GET', '/settings/security');
    if (!d) return;
    document.getElementById('allowedIps').value = (d.allowed_ips || []).join('\n');
    document.getElementById('myIpDisplay').textContent = d.my_ip || '-';
  } catch(e) { settingsMsg('ipMsg', 'danger', e.message); }
}

function _taLines(id) {
  return document.getElementById(id).value.split('\n').map(s => s.trim()).filter(Boolean);
}

function addMyIp() {
  const ip = document.getElementById('myIpDisplay').textContent;
  if (!ip || ip === '-') return;
  const el = document.getElementById('allowedIps');
  const lines = el.value.split('\n').map(s => s.trim()).filter(Boolean);
  if (!lines.includes(ip)) { lines.push(ip); el.value = lines.join('\n'); }
}

async function saveAllowedIps() {
  const allowed_ips = _taLines('allowedIps');
  const myIp = document.getElementById('myIpDisplay').textContent;
  if (allowed_ips.length > 0 && myIp && myIp !== '-') {
    const covered = allowed_ips.some(e => e.includes('/') || e === myIp);
    if (!covered && !confirm(`현재 접속 IP(${myIp})가 허용 목록에 없습니다.\n저장하면 이 IP에서 접속이 차단됩니다. 계속하시겠습니까?`)) return;
  }
  try {
    await api('PUT', '/settings/allowed-ips', {allowed_ips});
    settingsMsg('ipMsg', 'success', '접속 허용 IP가 저장되었습니다.');
  } catch(e) { settingsMsg('ipMsg', 'danger', e.message); }
}

// ── 고객 계정 관리 (admin 전용) ───────────────────────────────────────────────
// ── API 키 (조회 전용 토큰) ────────────────────────────────────────────────
let _apiKeyOwners = [];   // [{id: null|number, label}] — 발급 대상 선택지

function _renderApiKeyOwners(users) {
  _apiKeyOwners = [{ id: '', label: '관리자 (전체 조회)' }]
    .concat((users || []).map(u => ({ id: u.id, label: `${u.username} (${u.customer || '-'})` })));
  const sel = document.getElementById('apiKeyOwner');
  const keep = sel.value;
  sel.innerHTML = _apiKeyOwners.map(o => `<option value="${o.id}">${esc(o.label)}</option>`).join('');
  if (keep) sel.value = keep;
}

async function loadApiKeys() {
  const tbody = document.getElementById('apiKeyList');
  try {
    const keys = await api('GET', '/api-keys');
    if (!keys || !keys.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-muted small p-3">발급된 키가 없습니다.</td></tr>';
      return;
    }
    const now = Date.now();
    tbody.innerHTML = keys.map(k => {
      const expired = k.expires_at && new Date(k.expires_at).getTime() <= now;
      const badge = !k.is_active
        ? '<span class="badge bg-secondary-subtle text-secondary">폐기</span>'
        : (expired ? '<span class="badge bg-warning-subtle text-warning">만료</span>'
                   : '<span class="badge bg-success-subtle text-success">활성</span>');
      const owner = k.role === 'admin'
        ? `${esc(k.username)} <span class="badge bg-primary-subtle text-primary">관리자</span>`
        : `${esc(k.username)} <span class="text-muted small">${esc(k.customer || '-')}</span>`;
      return `<tr>
        <td class="font-monospace small">${esc(k.key_prefix)}…</td>
        <td class="small">${owner}</td>
        <td class="small">${esc(k.label || '-')}</td>
        <td class="small text-muted">${k.last_used_at ? timeAgo(k.last_used_at) : '미사용'}</td>
        <td class="small text-muted">${k.expires_at ? fmtLocalDate(new Date(k.expires_at)) : '무기한'}</td>
        <td>${badge}</td>
        <td class="text-end">
          <button class="btn btn-xs btn-outline-secondary" onclick="toggleApiKey(${k.id}, ${k.is_active ? 'false' : 'true'})">${k.is_active ? '폐기' : '재활성'}</button>
          <button class="btn btn-xs btn-outline-danger" onclick="deleteApiKey(${k.id}, '${esc(k.key_prefix)}')"><i class="bi bi-trash"></i></button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    settingsMsg('apiKeyMsg', 'danger', e.message);
    tbody.innerHTML = '';
  }
}

async function createApiKey() {
  const owner = document.getElementById('apiKeyOwner').value;
  const label = document.getElementById('apiKeyLabel').value.trim();
  const expires = document.getElementById('apiKeyExpires').value;
  const body = { user_id: owner === '' ? null : parseInt(owner, 10), label };
  // 만료일은 그 날 끝까지 유효하도록 다음 날 00:00 으로 보낸다
  if (expires) {
    const d = new Date(expires + 'T00:00:00');
    d.setDate(d.getDate() + 1);
    body.expires_at = d.toISOString();
  }
  try {
    const k = await api('POST', '/api-keys', body);
    document.getElementById('apiKeyIssuedValue').value = k.key;
    document.getElementById('apiKeyIssued').classList.remove('d-none');
    document.getElementById('apiKeyLabel').value = '';
    document.getElementById('apiKeyExpires').value = '';
    settingsMsg('apiKeyMsg', 'success', `${k.username} 계정의 키를 발급했습니다.`);
    loadApiKeys();
  } catch (e) { settingsMsg('apiKeyMsg', 'danger', e.message); }
}

function copyApiKey() {
  const el = document.getElementById('apiKeyIssuedValue');
  el.select();
  navigator.clipboard.writeText(el.value)
    .then(() => settingsMsg('apiKeyMsg', 'success', '키를 복사했습니다.'))
    .catch(() => settingsMsg('apiKeyMsg', 'warning', '복사에 실패했습니다. 직접 선택해 복사하세요.'));
}

async function toggleApiKey(id, active) {
  try {
    await api('PUT', `/api-keys/${id}/status?active=${active}`);
    loadApiKeys();
  } catch (e) { settingsMsg('apiKeyMsg', 'danger', e.message); }
}

async function deleteApiKey(id, prefix) {
  if (!confirm(`키 ${prefix}… 를 삭제할까요? 이 키를 쓰는 연동은 즉시 중단됩니다.`)) return;
  try {
    await api('DELETE', `/api-keys/${id}`);
    loadApiKeys();
  } catch (e) { settingsMsg('apiKeyMsg', 'danger', e.message); }
}

let _users = [];          // 목록 캐시 — 수정 모달이 현재 값을 채울 때 쓴다

async function loadUsers() {
  const tbody = document.getElementById('userList');
  document.getElementById('apiKeyIssued').classList.add('d-none');
  loadApiKeys();
  try {
    const [users, groups] = await Promise.all([
      api('GET', '/users'),
      api('GET', '/groups').catch(() => []),
    ]);
    _renderApiKeyOwners(users);
    // 고객사 자동완성: 그룹의 customer 값 중복 제거
    const customers = [...new Set((groups || []).map(g => g.customer).filter(Boolean))].sort();
    document.getElementById('customerOptions').innerHTML =
      customers.map(c => `<option value="${esc(c)}">`).join('');

    _users = users.filter(u => u.role === 'customer');
    if (!_users.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">등록된 고객 계정이 없습니다.</td></tr>';
      return;
    }
    tbody.innerHTML = _users.map(u => {
      const ips = u.allowed_ips && u.allowed_ips.length ? u.allowed_ips.join(', ') : '<span class="text-muted">제한 없음</span>';
      const badge = u.is_active
        ? '<span class="badge bg-success-subtle text-success">활성</span>'
        : '<span class="badge bg-secondary-subtle text-secondary">비활성</span>';
      return `<tr>
        <td class="ps-3 fw-semibold">${esc(u.username)}</td>
        <td>${esc(u.customer || '-')}</td>
        <td class="small">${ips}</td>
        <td>${badge}</td>
        <td class="text-end pe-3">
          <button class="btn btn-xs btn-outline-secondary" onclick="viewCustomerLogs('${esc(u.customer || '')}')" title="이 고객사의 접근 계정별 사용 내역 보기">사용 내역</button>
          <button class="btn btn-xs btn-outline-primary" onclick="openUserEdit(${u.id})" title="고객사·허용 IP·비밀번호 수정">수정</button>
          <button class="btn btn-xs btn-outline-secondary" onclick="toggleUser(${u.id}, ${u.is_active ? 'false' : 'true'})">${u.is_active ? '비활성화' : '활성화'}</button>
          <button class="btn btn-xs btn-outline-danger" onclick="deleteUser(${u.id}, '${esc(u.username)}')"><i class="bi bi-trash"></i></button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="text-danger small p-3">${esc(e.message)}</td></tr>`;
  }
}

async function createUser() {
  const username = document.getElementById('newUserName').value.trim();
  const password = document.getElementById('newUserPwd').value;
  const customer = document.getElementById('newUserCustomer').value.trim();
  const allowed_ips = _taLines('newUserIps');
  if (!username || !customer) { settingsMsg('userMsg', 'danger', '아이디와 고객사는 필수입니다.'); return; }
  if (password.length < 8) { settingsMsg('userMsg', 'danger', '비밀번호는 8자 이상이어야 합니다.'); return; }
  try {
    await api('POST', '/users', {username, password, customer, allowed_ips});
    settingsMsg('userMsg', 'success', `'${username}' 계정이 생성되었습니다.`);
    document.getElementById('newUserName').value = '';
    document.getElementById('newUserPwd').value = '';
    document.getElementById('newUserCustomer').value = '';
    document.getElementById('newUserIps').value = '';
    loadUsers();
  } catch (e) { settingsMsg('userMsg', 'danger', e.message); }
}

async function toggleUser(id, active) {
  try {
    await api('PUT', `/users/${id}`, {is_active: active});
    loadUsers();
  } catch (e) { settingsMsg('userMsg', 'danger', e.message); }
}

// 고객사·허용 IP·비밀번호·활성 상태를 한 모달에서 수정한다 (PUT /users/{id}).
function openUserEdit(id) {
  const u = _users.find(x => x.id === id);
  if (!u) return;
  document.getElementById('editUserId').value = u.id;
  document.getElementById('editUserName').textContent = u.username;
  document.getElementById('editUserCustomer').value = u.customer || '';
  document.getElementById('editUserIps').value = (u.allowed_ips || []).join('\n');
  document.getElementById('editUserPwd').value = '';
  document.getElementById('editUserActive').checked = !!u.is_active;
  document.getElementById('editUserMsg').classList.add('d-none');
  bootstrap.Modal.getOrCreateInstance(document.getElementById('userEditModal')).show();
}

async function saveUserEdit() {
  const id = parseInt(document.getElementById('editUserId').value, 10);
  const customer = document.getElementById('editUserCustomer').value.trim();
  const pw = document.getElementById('editUserPwd').value;
  if (!customer) { settingsMsg('editUserMsg', 'danger', '고객사는 비울 수 없습니다.'); return; }
  if (pw && pw.length < 8) { settingsMsg('editUserMsg', 'danger', '비밀번호는 8자 이상이어야 합니다.'); return; }
  const body = {
    customer,
    allowed_ips: _taLines('editUserIps'),
    is_active: document.getElementById('editUserActive').checked,
  };
  if (pw) body.password = pw;
  try {
    await api('PUT', `/users/${id}`, body);
    bootstrap.Modal.getInstance(document.getElementById('userEditModal'))?.hide();
    settingsMsg('userMsg', 'success', '계정 정보를 수정했습니다.');
    loadUsers();
  } catch (e) { settingsMsg('editUserMsg', 'danger', e.message); }
}

async function deleteUser(id, username) {
  if (!confirm(`'${username}' 계정을 삭제하시겠습니까?`)) return;
  try {
    await api('DELETE', `/users/${id}`);
    loadUsers();
  } catch (e) { settingsMsg('userMsg', 'danger', e.message); }
}


// ── 관리자 계정 ─────────────────────────────────────────────────────────────
// 고객 계정과 같은 /users API 를 쓰고 role 로만 나눈다 (규칙이 두 벌이 되지 않게).
let _admins = [];

function _lockBadge(u) {
  if (!u.is_active) return '<span class="badge bg-secondary-subtle text-secondary">비활성</span>';
  if (u.locked_seconds > 0) {
    const min = Math.ceil(u.locked_seconds / 60);
    return `<span class="badge bg-danger-subtle text-danger">잠김 (${min}분 남음)</span>`;
  }
  return '<span class="badge bg-success-subtle text-success">활성</span>';
}

async function loadAdmins() {
  const tbody = document.getElementById('adminList');
  try {
    const users = await api('GET', '/users');
    if (!users) return;
    _admins = users.filter(u => u.role === 'admin');
    if (!_admins.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">관리자 계정이 없습니다.</td></tr>';
      return;
    }
    tbody.innerHTML = _admins.map(u => `
      <tr>
        <td class="ps-3 fw-semibold">${esc(u.username)}</td>
        <td>${_lockBadge(u)}</td>
        <td class="small text-muted">${u.last_login_at ? fmtLocalDateTime(new Date(u.last_login_at)) : '-'}</td>
        <td class="text-end pe-3">
          ${u.locked_seconds > 0 ? `<button class="btn btn-xs btn-outline-warning" onclick="unlockAdmin(${u.id})" title="잠금 해제">해제</button>` : ''}
          <button class="btn btn-xs btn-outline-secondary" onclick="resetAdminPwd(${u.id}, '${esc(u.username)}')" title="비밀번호 재설정"><i class="bi bi-key"></i></button>
          <button class="btn btn-xs btn-outline-secondary" onclick="toggleAdmin(${u.id}, ${u.is_active ? 'false' : 'true'})">${u.is_active ? '비활성화' : '활성화'}</button>
          <button class="btn btn-xs btn-outline-danger" onclick="deleteAdmin(${u.id}, '${esc(u.username)}')"><i class="bi bi-trash"></i></button>
        </td>
      </tr>`).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="text-danger small p-3">${esc(e.message)}</td></tr>`;
  }
}

async function createAdmin() {
  const username = document.getElementById('newAdminName').value.trim();
  const password = document.getElementById('newAdminPwd').value;
  if (!username) { settingsMsg('adminMsg', 'danger', '아이디는 필수입니다.'); return; }
  if (password.length < 8) { settingsMsg('adminMsg', 'danger', '비밀번호는 8자 이상이어야 합니다.'); return; }
  try {
    await api('POST', '/users', {username, password, role: 'admin'});
    settingsMsg('adminMsg', 'success', `'${username}' 관리자 계정이 생성되었습니다.`);
    document.getElementById('newAdminName').value = '';
    document.getElementById('newAdminPwd').value = '';
    loadAdmins();
  } catch (e) { settingsMsg('adminMsg', 'danger', e.message); }
}

async function toggleAdmin(id, active) {
  try {
    await api('PUT', `/users/${id}`, {is_active: active});
    loadAdmins();
  } catch (e) { settingsMsg('adminMsg', 'danger', e.message); }
}

async function unlockAdmin(id) {
  try {
    await api('POST', `/users/${id}/unlock`);
    settingsMsg('adminMsg', 'success', '잠금을 해제했습니다.');
    loadAdmins();
  } catch (e) { settingsMsg('adminMsg', 'danger', e.message); }
}

async function resetAdminPwd(id, username) {
  const pw = prompt(`'${username}' 계정의 새 비밀번호 (8자 이상):`);
  if (pw === null) return;
  if (pw.length < 8) { settingsMsg('adminMsg', 'danger', '비밀번호는 8자 이상이어야 합니다.'); return; }
  try {
    await api('PUT', `/users/${id}`, {password: pw});
    settingsMsg('adminMsg', 'success', `'${username}' 비밀번호를 변경했습니다 (잠금도 해제됨).`);
    loadAdmins();
  } catch (e) { settingsMsg('adminMsg', 'danger', e.message); }
}

async function deleteAdmin(id, username) {
  if (!confirm(`'${username}' 관리자 계정을 삭제하시겠습니까?`)) return;
  try {
    await api('DELETE', `/users/${id}`);
    loadAdmins();
  } catch (e) { settingsMsg('adminMsg', 'danger', e.message); }
}

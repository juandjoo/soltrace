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
    st.innerHTML = `<span class="text-danger">원격 확인 실패: ${v.error}</span>`;
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
            <span>${t.name}</span>
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
}

async function toggleMute(muted) {
  const msg = document.getElementById('notifyMsg');
  try {
    await api('POST', `/settings/notify/mute?muted=${muted}`);
    _applyMuteState(muted);
    msg.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>알림 ${muted ? '중지' : '재개'}됨</span>`;
  } catch(e) {
    document.getElementById('notifyMuteToggle').checked = !muted;
    msg.innerHTML = `<span class="text-danger">변경 실패: ${e.message}</span>`;
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
    msg.innerHTML = `<span class="text-danger">저장 실패: ${e.message}</span>`;
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
    msg.innerHTML = `<span class="text-danger">발송 실패: ${e.message}</span>`;
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

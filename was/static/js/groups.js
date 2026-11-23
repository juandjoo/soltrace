// ── 그룹 설정 모달·삭제·전체 업데이트 ─────────────────────────────────────
// 그룹 목록은 장비·그룹 화면(device-view.js)의 왼쪽에 있다. 여기는 그 화면이 여는 모달과 동작만.

// 그룹 목록의 purge 배지 — 사용 중인 ID 수 / 전체. 그룹을 끄면 회색 '꺼짐'.
function purgeBadge(g) {
  const ts = g.purge_targets || [];
  if (!ts.length) return '';
  const on = ts.filter(t => t.enabled !== false).length;
  const tip = ts.map(t => `${t.enabled !== false ? '●' : '○'} ${t.ftp_username}${t.strip_prefix ? ` ${t.strip_prefix}` : ''} → ${t.service}/${t.volume}`).join('\n');
  if (g.purge_active === false)
    return ` <span class="badge bg-light text-muted border fw-normal" title="업로드 자동 purge (그룹 꺼짐)&#10;${esc(tip)}">purge 꺼짐</span>`;
  return ` <span class="badge bg-info-subtle text-info-emphasis border fw-normal" title="업로드 자동 purge&#10;${esc(tip)}">purge ${on}/${ts.length}</span>`;
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
  document.getElementById('groupPurgeActive').checked = g ? g.purge_active !== false : true;
  renderPurgeTargetRows(g?.purge_targets || []);
  await getTelcos();
  populateGroupTelco(g?.telco || '');
  document.getElementById('groupModalTitle').textContent = id ? '그룹 수정' : '그룹 추가';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('groupModal')).show();
}

// ── 업로드 자동 purge — 업로드 ID별 카드 ───────────────────────────────────
// 윗줄 감지(FTP ID · 마운트 경로), 아랫줄 API(계정 · 비밀번호 · 서비스 · 볼륨).
// 비밀번호는 서버가 돌려주지 않는다 — 저장돼 있으면 비워 둔 채 '바꿀 때만 입력'.
// 같은 FTP ID 도 마운트 경로가 다르면 카드를 따로 둔다('경로 추가') — 업로드 경로에 맞는 것 중
// 가장 긴 마운트 경로의 서비스·볼륨으로 purge 한다. 같은 API 계정이면 비밀번호는 다시 넣지 않아도 된다.
function _purgeCardHtml(t = {}) {
  const on = t.enabled !== false;
  const field = (col, lbl, cls, v, ph, extra = '') =>
    `<div class="${col}"><label class="pt-lbl">${lbl}</label><input class="form-control form-control-sm ${cls}" value="${esc(v || '')}" placeholder="${ph}" ${extra}></div>`;
  const tokenInfo = t.api_token_at
    ? `<span class="badge bg-success-subtle text-success-emphasis border fw-normal"><i class="bi bi-check-circle me-1"></i>토큰 발급 ${esc(fmtLocalDateTime(new Date(t.api_token_at)))}</span>`
    : (t.ftp_username ? '<span class="text-muted small">토큰 미발급</span>' : '');
  return `<div class="pt-card${on ? '' : ' off'}" data-id="${t.id || ''}">
    <div class="d-flex align-items-center flex-wrap gap-2 mb-2">
      <div class="form-check form-switch m-0" title="미사용이면 설정은 남기고 감지·purge 만 멈춥니다">
        <input class="form-check-input pt-enabled" type="checkbox" ${on ? 'checked' : ''} onchange="syncPurgeCardStates()">
        <label class="form-check-label small pt-enabled-label">${on ? '사용' : '미사용'}</label>
      </div>
      <span class="pt-name"><i class="bi bi-person me-1"></i><span class="pt-name-text">${esc(t.ftp_username || '새 ID')}</span></span>
      <span class="pt-msg">${tokenInfo}</span>
      <div class="ms-auto text-nowrap">
        <button type="button" class="btn btn-xs btn-outline-primary" onclick="addPurgeMountCard(this)" title="같은 FTP ID·API 계정으로 다른 마운트 경로 추가"><i class="bi bi-signpost-split me-1"></i>경로 추가</button>
        <button type="button" class="btn btn-xs btn-outline-success" onclick="testPurgeTargetToken(this)" title="입력 중인 값으로 토큰 발급 시험"><i class="bi bi-key me-1"></i>토큰 테스트</button>
        <button type="button" class="btn btn-xs btn-outline-danger" onclick="removePurgeCard(this)" title="이 ID 삭제(저장해야 반영)"><i class="bi bi-trash"></i></button>
      </div>
    </div>
    <div class="row g-2 pt-fields">
      ${field('col-md-3', 'FTP ID', 'pt-user', t.ftp_username, 'FTP ID', 'oninput="this.closest(\'.pt-card\').querySelector(\'.pt-name-text\').textContent = this.value || \'새 ID\'"')}
      ${field('col-md-9', '마운트 경로', 'pt-strip font-monospace', t.strip_prefix, '/mnt1/8000000000000004549/cjenm')}
      ${field('col-6 col-md-3', 'API 계정', 'pt-api-user', t.api_username, 'API 계정')}
      <div class="col-6 col-md-3"><label class="pt-lbl">API 비밀번호</label><input class="form-control form-control-sm pt-api-pass" type="password" autocomplete="new-password" placeholder="${t.has_api_password ? '••••• 저장됨' : '비밀번호'}"></div>
      ${field('col-6 col-md-3', '서비스', 'pt-svc', t.service, '서비스명')}
      ${field('col-6 col-md-3', '볼륨', 'pt-vol', t.volume, '볼륨')}
    </div>
  </div>`;
}

function renderPurgeTargetRows(targets) {
  document.getElementById('purgeTargetRows').innerHTML = targets.map(_purgeCardHtml).join('');
  syncPurgeCardStates();
}

function addPurgeTargetRow() {
  document.getElementById('purgeTargetRows').insertAdjacentHTML('beforeend', _purgeCardHtml());
  syncPurgeCardStates();
}

// 같은 FTP ID·API 계정을 채운 새 카드를 바로 아래에 — 마운트 경로·서비스·볼륨만 새로 넣는다
function addPurgeMountCard(btn) {
  const card = btn.closest('.pt-card');
  const val = cls => card.querySelector(cls).value.trim();
  const draft = {ftp_username: val('.pt-user'), api_username: val('.pt-api-user'),
                 has_api_password: !!val('.pt-api-pass') || card.querySelector('.pt-api-pass').placeholder.includes('저장됨')};
  card.insertAdjacentHTML('afterend', _purgeCardHtml(draft));
  const added = card.nextElementSibling;
  // 입력 중인(아직 저장 안 한) 비밀번호는 그대로 옮긴다 — 저장된 것이면 서버가 같은 API 계정 값을 쓴다
  added.querySelector('.pt-api-pass').value = val('.pt-api-pass');
  added.querySelector('.pt-strip').focus();
  syncPurgeCardStates();
}

function removePurgeCard(btn) {
  btn.closest('.pt-card').remove();
  syncPurgeCardStates();
}

// 스위치 상태를 화면에 반영 — ID 스위치 라벨·흐림, 그룹이 꺼지면 모든 카드를 흐리게, ID 수 배지
function syncPurgeCardStates() {
  const groupOn = document.getElementById('groupPurgeActive').checked;
  const cards = [...document.querySelectorAll('#purgeTargetRows .pt-card')];
  cards.forEach(card => {
    const on = card.querySelector('.pt-enabled').checked;
    card.querySelector('.pt-enabled-label').textContent = on ? '사용' : '미사용';
    card.classList.toggle('off', !on || !groupOn);
  });
  document.getElementById('purgeTargetCount').textContent = cards.length;
}

// 모든 칸이 빈 카드는 버린다(추가만 누르고 안 채운 카드)
function readPurgeTargetRows() {
  return [...document.querySelectorAll('#purgeTargetRows .pt-card')].map(card => ({
    id: Number(card.dataset.id) || null,
    enabled: card.querySelector('.pt-enabled').checked,
    ftp_username: card.querySelector('.pt-user').value.trim(),
    service: card.querySelector('.pt-svc').value.trim(),
    volume: card.querySelector('.pt-vol').value.trim(),
    strip_prefix: card.querySelector('.pt-strip').value.trim() || null,
    api_username: card.querySelector('.pt-api-user').value.trim() || null,
    api_password: card.querySelector('.pt-api-pass').value.trim() || null,
  })).filter(t => t.ftp_username || t.service || t.volume || t.strip_prefix || t.api_username || t.api_password);
}

// 카드의 '토큰 테스트' — 저장 전에 입력 중인 통신사·계정으로 발급해 본다. 비밀번호를 비우면 저장된 것을 쓴다.
async function testPurgeTargetToken(btn) {
  const card = btn.closest('.pt-card');
  const msg = card.querySelector('.pt-msg');
  const show = (kind, text) => {
    const cls = {success: 'bg-success-subtle text-success-emphasis', danger: 'bg-danger-subtle text-danger-emphasis', muted: 'bg-light text-muted'}[kind];
    const icon = {success: 'bi-check-circle', danger: 'bi-exclamation-circle', muted: 'bi-hourglass-split'}[kind];
    msg.innerHTML = `<span class="badge ${cls} border fw-normal text-wrap text-start"><i class="bi ${icon} me-1"></i>${esc(text)}</span>`;
  };
  const body = {
    telco: document.getElementById('groupTelco').value,
    api_username: card.querySelector('.pt-api-user').value.trim(),
    api_password: card.querySelector('.pt-api-pass').value.trim() || null,
    group_id: Number(document.getElementById('groupId').value) || null,
    target_id: Number(card.dataset.id) || null,
  };
  if (!body.telco) return show('danger', '그룹 통신사를 먼저 지정하세요.');
  if (!body.api_username) return show('danger', 'API 계정을 입력하세요.');
  show('muted', '토큰 발급 중…');
  try {
    const r = await api('POST', '/groups/token-test', body);
    show(r.ok ? 'success' : 'danger', r.message);
  } catch (e) { show('danger', e.message); }
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
    purge_active: document.getElementById('groupPurgeActive').checked,
    purge_targets: readPurgeTargetRows(),
  };
  if (!body.name) return alert('그룹명을 입력하세요.');
  const badRow = body.purge_targets.find(t => !t.ftp_username || !t.service || !t.volume);
  if (badRow) return alert('업로드 ID마다 FTP ID · 서비스 · 볼륨을 모두 입력하세요.');
  if (body.purge_targets.length && !body.telco) return alert('purge 를 쓰려면 통신사를 지정하세요 — 그 통신사의 API 로 보냅니다.');
  const noAccount = body.purge_targets.find(t => !t.api_username);
  if (noAccount) return alert(`'${noAccount.ftp_username}' 의 API 계정을 입력하세요.`);
  try {
    if (id) {
      await api('PUT', `/groups/${id}`, body);
    } else {
      const created = await api('POST', '/groups', body);
      if (created?.id) _dvView = created.id;   // 방금 만든 그룹을 바로 보여준다
    }
    bootstrap.Modal.getInstance(document.getElementById('groupModal')).hide();
    loadDevices();
  } catch(e) { alert(e.message); }
}

async function deleteGroup(id) {
  const g = allGroups.find(x => x.id === id);
  const n = g?.device_count || 0;
  if (!confirm(`${g?.name || '그룹'} 을(를) 삭제하시겠습니까?${n ? `\n소속 장비 ${n}대는 미배정이 됩니다(장비·로그는 남습니다).` : ''}`)) return;
  try {
    await api('DELETE', `/groups/${id}`);
  } catch (e) { alert('그룹 삭제 실패: ' + e.message); return; }
  if (_dvView === id) _dvView = 'all';
  loadDevices();
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

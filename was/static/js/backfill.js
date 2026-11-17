// ── 그룹 → 로그 재전송(backfill) ────────────────────────────────────────────
// 서버 규칙(기간 31일·보존 기간·동시 2대)은 app/backfill.py 한 곳에 있다. 여기서는
// 입력 편의만 돕고 판정은 서버 응답(400 메시지)을 그대로 보여준다.

const BACKFILL_MAX_DAYS = 31;
const BACKFILL_POLL_MS = 10000;
const BACKFILL_DEFAULT_PATTERNS = {Transfer: 'TransferLog*', Extended: 'ExtendedAllLog*'};
const BACKFILL_ACTIVE = new Set(['queued', 'sent', 'running']);
const BACKFILL_STATUS = {
  queued:  ['대기', 'bg-secondary'],
  sent:    ['시작 대기', 'bg-info text-dark'],
  running: ['진행 중', 'bg-primary'],
  done:    ['완료', 'bg-success'],
  failed:  ['실패', 'bg-danger'],
  skipped: ['제외', 'bg-light text-dark border'],
};
let _backfillTimer = null;

function openBackfill(groupId) {
  const g = allGroups.find(x => x.id === groupId);
  if (!g) return;
  document.getElementById('bfGroupId').value = groupId;
  document.getElementById('bfGroupName').textContent = g.name;
  const today = new Date();
  const weekAgo = new Date(today.getTime() - 6 * 86400000);
  const from = document.getElementById('bfFrom');
  const to = document.getElementById('bfTo');
  from.value = fmtLocalDate(weekAgo);
  to.value = fmtLocalDate(today);
  to.max = fmtLocalDate(today);
  from.max = fmtLocalDate(today);
  for (const [k, def] of Object.entries(BACKFILL_DEFAULT_PATTERNS)) {
    document.getElementById(`bfUse${k}`).checked = true;
    document.getElementById(`bf${k}Pattern`).value = def;
  }
  syncBackfillPatterns();
  document.getElementById('bfJobs').innerHTML = '<div class="text-muted small py-2">불러오는 중…</div>';
  const el = document.getElementById('backfillModal');
  el.addEventListener('hidden.bs.modal', _stopBackfillPoll, {once: true});
  bootstrap.Modal.getOrCreateInstance(el).show();
  loadBackfillJobs();
}

function syncBackfillPatterns() {
  for (const k of Object.keys(BACKFILL_DEFAULT_PATTERNS)) {
    document.getElementById(`bf${k}Pattern`).disabled = !document.getElementById(`bfUse${k}`).checked;
  }
}

// 체크를 끈 로그는 null(읽지 않음). 켰는데 비우면 기본 패턴.
function _backfillPattern(k) {
  if (!document.getElementById(`bfUse${k}`).checked) return null;
  return document.getElementById(`bf${k}Pattern`).value.trim() || BACKFILL_DEFAULT_PATTERNS[k];
}

function _stopBackfillPoll() {
  clearTimeout(_backfillTimer);
  _backfillTimer = null;
}

async function submitBackfill() {
  const groupId = document.getElementById('bfGroupId').value;
  const dateFrom = document.getElementById('bfFrom').value;
  const dateTo = document.getElementById('bfTo').value;
  if (!dateFrom || !dateTo) { alert('시작일과 종료일을 입력하세요.'); return; }
  const days = Math.round((new Date(dateTo) - new Date(dateFrom)) / 86400000) + 1;
  if (days < 1) { alert('시작일이 종료일보다 늦습니다.'); return; }
  if (days > BACKFILL_MAX_DAYS) { alert(`기간은 최대 ${BACKFILL_MAX_DAYS}일입니다 (선택 ${days}일).`); return; }
  const transferPattern = _backfillPattern('Transfer');
  const extendedPattern = _backfillPattern('Extended');
  if (!transferPattern && !extendedPattern) { alert('TransferLog·ExtendedAllLog 중 하나 이상을 선택하세요.'); return; }
  const files = [transferPattern, extendedPattern].filter(Boolean).join(', ');
  const name = document.getElementById('bfGroupName').textContent;
  if (!confirm(`${name} 그룹 장비의 ${dateFrom} ~ ${dateTo} (${days}일) 로그를 다시 받으시겠습니까?\n파일: ${files}\n이미 들어와 있는 로그는 걸러지고 빠진 것만 추가됩니다.`)) return;
  const btn = document.getElementById('bfSubmit');
  btn.disabled = true;
  try {
    const r = await api('POST', `/groups/${groupId}/backfill`, {
      date_from: dateFrom, date_to: dateTo,
      transfer_pattern: transferPattern, extended_pattern: extendedPattern,
    });
    if (!r) return;
    const skipped = r.skipped ? `\n${r.skipped}대는 제외되었습니다 — 사유는 아래 목록에서 확인하세요.` : '';
    alert(`${r.queued}대에 재전송을 요청했습니다.${skipped}`);
    loadBackfillJobs();
  } catch (e) {
    alert('재전송 요청 실패: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function loadBackfillJobs() {
  _stopBackfillPoll();
  const groupId = document.getElementById('bfGroupId').value;
  const box = document.getElementById('bfJobs');
  let jobs;
  try {
    jobs = await api('GET', `/groups/${groupId}/backfill`);
  } catch (e) {
    box.innerHTML = `<div class="text-danger small py-2">불러오기 실패: ${esc(e.message)}</div>`;
    return;
  }
  if (!jobs) return;
  box.innerHTML = jobs.length ? _backfillTable(jobs)
    : '<div class="text-muted small py-2">아직 요청한 재전송이 없습니다.</div>';
  // 진행 중인 작업이 있고 창이 열려 있을 때만 다시 읽는다
  const open = document.getElementById('backfillModal').classList.contains('show');
  if (open && jobs.some(j => BACKFILL_ACTIVE.has(j.status))) {
    _backfillTimer = setTimeout(loadBackfillJobs, BACKFILL_POLL_MS);
  }
}

function _backfillTable(jobs) {
  const rows = jobs.map(j => {
    const [label, cls] = BACKFILL_STATUS[j.status] || [j.status, 'bg-secondary'];
    const when = j.finished_at || j.updated_at;
    return `<tr>
      <td class="fw-semibold">${esc(j.hostname || `#${j.device_id}`)}</td>
      <td class="small">${esc(j.date_from)} ~ ${esc(j.date_to)}
        <div class="text-muted font-monospace" style="font-size:.75em">${[j.transfer_pattern, j.extended_pattern].filter(Boolean).map(esc).join(' · ')}</div></td>
      <td><span class="badge ${cls}">${label}</span></td>
      <td class="text-end">${j.status === 'skipped' ? '-' : Number(j.sent_count).toLocaleString()}</td>
      <td class="small text-muted">${fmtLocalDateTime(new Date(when))}</td>
      <td class="small" style="word-break:break-word">${j.message ? esc(j.message) : ''}</td>
    </tr>`;
  }).join('');
  return `<div class="table-responsive"><table class="table table-sm align-middle mb-0">
    <thead class="table-light"><tr><th>장비</th><th>기간</th><th>상태</th>
      <th class="text-end" title="WAS 가 받은 건수 — 이미 있던 로그도 포함되며 중복은 저장 시 걸러진다">받은 건수</th>
      <th>갱신</th><th>비고</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

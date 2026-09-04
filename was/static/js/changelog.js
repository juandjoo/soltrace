// 변경 이력 — 배포된 저장소의 changelog.md 를 날짜별로 보여준다.
// 현재 버전(설정>업데이트와 같은 /settings/version)을 함께 표시해, 지금 돌고 있는
// 코드가 이력 어디쯤인지 바로 알 수 있게 한다.
// 탭에 들어올 때마다 새로 받는다 — 업데이트 직후에도 최신 이력이 보이도록
// (응답이 작아 캐시할 이유가 없다).
function initChangelogPage() {
  loadChangelog();
}

async function loadChangelog() {
  const body = document.getElementById('clBody');
  const cur = document.getElementById('clCurrent');
  try {
    const [entries, ver] = await Promise.all([
      api('GET', '/settings/changelog'),
      api('GET', '/settings/version'),
    ]);
    cur.textContent = ver
      ? `현재 ${ver.branch || '-'} · ${ver.commit || '-'} (${ver.commit_date || '-'})`
      : '';

    if (!entries || !entries.length) {
      body.innerHTML = '<span class="text-muted">changelog.md 를 찾을 수 없습니다.</span>';
      return;
    }
    const curCommit = (ver?.commit || '').trim();
    body.innerHTML = entries.map(e => `
      <div class="mb-3">
        <div class="fw-semibold mb-1" style="font-size:0.9rem">
          <i class="bi bi-calendar3 me-1 text-muted"></i>${esc(e.date)}
          <span class="text-muted fw-normal">· ${e.items.length}건</span>
        </div>
        <ul class="list-unstyled mb-0 ps-3">
          ${e.items.map(it => {
            const isCur = it.commit && curCommit && it.commit.startsWith(curCommit);
            return `<li class="d-flex gap-2 py-1" style="border-bottom:1px solid var(--st-border)">
              <span class="flex-grow-1">${esc(it.text)}</span>
              ${it.commit ? `<code class="text-muted flex-shrink-0" style="font-size:0.72rem">${esc(it.commit)}</code>` : ''}
              ${isCur ? '<span class="badge bg-success flex-shrink-0">현재</span>' : ''}
            </li>`;
          }).join('')}
        </ul>
      </div>`).join('');
  } catch (e) {
    body.innerHTML = `<span class="text-danger">불러오지 못했습니다: ${esc(e.message)}</span>`;
  }
}

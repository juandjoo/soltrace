/* API 가이드 페이지 — 조회 전용 API 키로 데이터를 가져가는 방법 안내.
 *
 * 표와 예시는 아래 표에서 렌더링한다. 엔드포인트가 늘면 여기만 고치면 되고,
 * 예시의 주소는 현재 접속 주소(location.origin)를 그대로 써서 복사하면 바로 동작한다.
 */

const API_SAMPLE_KEY = 'slt_여기에_발급받은_키';

// 조회 전용 키로 호출 가능한 엔드포인트 (GET 만)
const API_ENDPOINTS = [
  ['/api/v1/logs', '로그 목록. 페이징(<code>page</code>, <code>size</code>)과 아래 필터를 조합한다. 총건수는 포함되지 않는다.'],
  ['/api/v1/logs/count', '위와 같은 필터의 <b>정확한 총건수</b>. 목록과 따로 호출한다.'],
  ['/api/v1/logs/export', '필터에 걸린 전체 로그를 CSV 로 내려받는다(스트리밍).'],
  ['/api/v1/logs/export/xlsx', '같은 조건을 XLSX 로 내려받는다.'],
  ['/api/v1/dashboard', '기간 합계와 일별 추이, 상위 사용자/장비/그룹, 작업유형 분포.'],
  ['/api/v1/dashboard/hourly', '그룹별 시간대 추이.'],
  ['/api/v1/dashboard/users-hourly', '상위 사용자별 시간대 추이.'],
  ['/api/v1/dashboard/service-health', '서비스 영향도 — 장비 상태, 알림, 추이, 실패 합계.'],
  ['/api/v1/devices', '장비 목록과 데몬 상태.'],
  ['/api/v1/groups', '그룹 목록.'],
];

// /api/v1/logs 및 count/export 공통 파라미터
const API_PARAMS = [
  ['start_time', '2026-09-01T00:00:00Z', '조회 시작(ISO 8601). <b>미지정 시 최근 90일</b>로 제한된다.'],
  ['end_time', '2026-09-03T23:59:59Z', '조회 종료.'],
  ['device_id', '3', '특정 장비만.'],
  ['group_id', '2', '특정 그룹에 속한 장비만.'],
  ['username', 'vod_', 'FTP 계정 부분 일치.'],
  ['client_ip', '192.168.', '클라이언트 IP 부분 일치.'],
  ['file_path', '.mp4', '파일 경로/이름 부분 일치.'],
  ['action', 'upload', 'upload, download, delete, rename, login, logout, mkdir, rmdir, cwd_fail'],
  ['exclude_actions', 'login,logout', '제외할 작업(쉼표 구분). <code>action</code> 과 함께 쓰면 무시된다.'],
  ['status', 'fail', 'success 또는 fail.'],
  ['page / size', '1 / 50', '페이지 번호와 크기(최대 500). 목록 조회에만 해당.'],
];

const API_ERRORS = [
  ['401', '키가 없거나, 폐기·만료된 키. 계정이 비활성인 경우도 포함된다.'],
  ['403', '조회 전용 키로 변경(POST/PUT/DELETE)을 시도했거나, 권한 밖의 관리 API 를 호출했다.'],
  ['404', '없는 경로. 문서(<code>/openapi.json</code>)는 비공개라 조회되지 않는다.'],
  ['422', '파라미터 형식 오류(예: 잘못된 날짜).'],
  ['503', '요청 한도 초과(분당 60회). 잠시 후 재시도한다.'],
];

function _apiBase() {
  // 데모(file://)로 열었을 때는 예시 주소를 알아볼 수 있게 대체한다
  return location.protocol.startsWith('http') ? location.origin : 'https://soltrace.example.com';
}

function _apiExamples() {
  const base = _apiBase();
  return [
    ['최근 24시간 업로드 실패', `curl -H "X-API-Key: ${API_SAMPLE_KEY}" \\
  "${base}/api/v1/logs?action=upload&status=fail&size=50"`],
    ['같은 조건의 총건수', `curl -H "X-API-Key: ${API_SAMPLE_KEY}" \\
  "${base}/api/v1/logs/count?action=upload&status=fail"`],
    ['기간 지정 + CSV 저장', `curl -H "X-API-Key: ${API_SAMPLE_KEY}" \\
  "${base}/api/v1/logs/export?start_time=2026-09-01T00:00:00Z&end_time=2026-09-02T00:00:00Z" \\
  -o logs.csv`],
    ['최근 7일 대시보드 요약', `curl -H "X-API-Key: ${API_SAMPLE_KEY}" \\
  "${base}/api/v1/dashboard?days=7"`],
    ['Python', `import requests

r = requests.get(
    "${base}/api/v1/logs",
    headers={"X-API-Key": "${API_SAMPLE_KEY}"},
    params={"action": "upload", "status": "fail", "size": 50},
    timeout=30,
)
r.raise_for_status()
for row in r.json()["items"]:
    print(row["log_time"], row["username"], row["file_path"])`],
  ];
}

// 코드 블록 + 복사 버튼
function _codeBlock(text) {
  return `<div class="api-code d-flex align-items-start gap-2">
    <code class="flex-grow-1">${esc(text)}</code>
    <button class="btn btn-xs btn-outline-secondary flex-shrink-0" onclick="copyApiSnippet(this)" title="복사"><i class="bi bi-clipboard"></i></button>
  </div>`;
}

function copyApiSnippet(btn) {
  const text = btn.parentElement.querySelector('code').textContent;
  navigator.clipboard.writeText(text).then(() => {
    const icon = btn.querySelector('i');
    icon.className = 'bi bi-check2';
    setTimeout(() => { icon.className = 'bi bi-clipboard'; }, 1500);
  });
}

function initApiGuide() {
  const base = _apiBase();
  document.getElementById('apiAuthHeader').textContent =
    `curl -H "X-API-Key: ${API_SAMPLE_KEY}" "${base}/api/v1/logs?size=10"`;
  document.getElementById('apiAuthBearer').textContent =
    `curl -H "Authorization: Bearer ${API_SAMPLE_KEY}" "${base}/api/v1/logs?size=10"`;

  document.getElementById('apiEndpointList').innerHTML = API_ENDPOINTS.map(
    ([path, desc]) => `<tr>
      <td><span class="badge bg-success-subtle text-success me-1">GET</span><code>${esc(path)}</code></td>
      <td class="text-muted">${desc}</td>
    </tr>`).join('');

  document.getElementById('apiParamList').innerHTML = API_PARAMS.map(
    ([name, sample, desc]) => `<tr>
      <td><code>${esc(name)}</code></td>
      <td class="text-muted font-monospace" style="font-size:.8rem">${esc(sample)}</td>
      <td class="text-muted">${desc}</td>
    </tr>`).join('');

  document.getElementById('apiErrorList').innerHTML = API_ERRORS.map(
    ([code, desc]) => `<tr><td><code>${esc(code)}</code></td><td class="text-muted">${desc}</td></tr>`
  ).join('');

  document.getElementById('apiExamples').innerHTML = _apiExamples().map(
    ([title, code]) => `<div class="mb-3">
      <div class="small fw-semibold mb-1">${esc(title)}</div>
      ${_codeBlock(code)}
    </div>`).join('');
}

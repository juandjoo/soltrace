# soltrace — 프로젝트 지침

구성·설치·운영·보안 결정은 `README.md` 가 정본이다(데몬은 `ftp-daemon/README.md`). 작업 영역에 맞는 절을 먼저 읽는다:
- 배포·스키마 적용 → "WAS 업데이트 › 배포 순서" (락 타임아웃·소유권 이관·복사는 재시작 직전)
- 캐시 → "보안 › 브라우저 캐시" (**아무것도 캐시하지 않는다** — `SecurityHeadersMiddleware` 한 곳)
- 고객사 데이터 격리 → "웹 UI 기능 › 고객사 계정"
- 파티션·디스크 → "DB 파티션 관리"
- 500 조사 → 앱 로그는 저널이 아니라 `/var/log/soltrace/error.log`·`access.log`·`selfupdate.log`

## 커밋·푸시 (다른 프로젝트와 다른 점)
- **원격 이름이 반대다:** `origin` = GitLab(`gitlab.solbox.com`), `github` = GitHub. GitHub 푸시는 `git push github main`.
  GitLab(`origin`)은 best-effort — 실패해도 GitHub 성공으로 완료.
- 커밋 메시지는 접두어 없이 **"무엇을 — 왜"** 한국어 한 줄이다(예: `cron 이 만든 파티션도 앱 소유로 — DB 저장소 화면이 500 이었다`).
- 기능 커밋 뒤에 **별도 커밋** `changelog 갱신` 으로 `changelog.md` 최상단에 추가한다.
- 테스트: `cd was && .venv/bin/python -m pytest tests -q`.

## 규칙 (요청 이력에서 반복된 지적 — 2026-10-17)

### 1. DB 객체는 앱 계정 소유로 만든다
cron(`create_partitions.sh`)이 postgres 로 만든 파티션 때문에 DB 저장소 화면이 500, 자동 정리 DROP 도 실패했다(`5a3da8a`).
앱 밖(cron·수동 SQL·설치 스크립트)에서 테이블·파티션·인덱스를 만드는 코드를 추가하면 생성 직후 공용 셸 함수 `soltrace_fix_owner`(`scripts/db_migrate.sh`, `create_partitions.sh` 가 쓰는 방식)로 이관한다.

### 2. '실패'는 실제 사용자 영향이 있는 것만 보여준다
CWD 실패 오탐("실제 폴더 이동 실패가 아니면 표시하지 말자"), 같은 시각 업로드와 1초 차이로 못 거른 CWD 실패, 경로 없는 실패·`/vod//` 실패가 반복 지적됐다.
- 파서를 넓히거나 새 실패 유형을 집계에 넣을 때는 **실제 운영 로그 샘플로 오탐을 먼저 확인**한다.
- 경로가 비었거나 정상화된 경로(`//`)가 이상한 행은 집계에 넣기 전에 원인을 확인한다.

### 3. 웹 업데이트 후에는 버전과 화면을 직접 확인한다
"웹에서 업데이트하니 500, 서버에서 재시작하니 정상"(09-03), "업데이트했는데 1.0.0 그대로"(09-07)가 있었다.
배포 스크립트(`update_rocky8.sh`·`soltrace-selfupdate.sh`·`db_migrate.sh`)를 바꾸면 **웹 자가 업데이트 경로로도** 한 번 돌려 보고,
설정 › 변경 이력의 **현재** 배지와 장비 관리의 데몬 버전이 바뀌었는지 확인한다.

#!/usr/bin/env bash
# SolTrace 자가 업데이트 래퍼 (root 전용).
#
# 보안 경계:
#   - 이 파일은 root 소유로 /usr/local/sbin (soltrace 가 수정 불가한 위치)에 설치되고,
#     sudoers 로 soltrace 가 "인자 없이"만 실행하도록 제한된다.
#   - 배포 로직을 이 스크립트 안에 자체 포함한다 (repo 안의 스크립트를 root 로
#     실행하지 않음). 코드는 항상 git reset --hard origin/<branch> 로 원격에서만
#     받으므로 로컬 변조가 배포에 반영되지 않는다.
#
# 흐름:
#   WAS(soltrace) → sudo -n /usr/local/sbin/soltrace-selfupdate (인자 없음)
#     → systemd-run 으로 분리 유닛을 띄우고 즉시 반환
#     → 분리 유닛이 이 스크립트를 --run 으로 호출해 실제 배포 수행
#       (WAS 재시작에도 호출자 cgroup 과 분리돼 끝까지 진행)
#
# 배포 순서 원칙:
#   새 코드(app/static)는 재시작 직전에 복사한다. 복사 시점부터 재시작까지는
#   "디스크는 새 코드 / 프로세스는 옛 코드"인 어긋난 상태이고, unit 의
#   --max-requests 로 워커가 재활용되면 새 코드가 옛 스키마·옛 의존성 위에서
#   기동해 500 이 난다. 의존성·스키마·nginx 를 먼저 끝내고 마지막에 복사+재시작.
set -eEuo pipefail

REPO_DIR="/opt/soltrace"
DEPLOY_DIR="/opt/soltrace"
BRANCH="main"
LOG="/var/log/soltrace/selfupdate.log"

if [ "${1:-}" != "--run" ]; then
    exec systemd-run \
        --no-block \
        --unit="soltrace-selfupdate" \
        --collect \
        --property=Type=oneshot \
        --setenv=PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        /usr/local/sbin/soltrace-selfupdate --run
fi

# ── 실제 배포 (분리 유닛에서 root 로 실행) ──────────────────────────────────
exec >> "$LOG" 2>&1
echo "[$(date '+%F %T')] selfupdate 시작 (branch=$BRANCH)"

# 배포 본문을 함수로 감싼다 — bash 는 최상위 명령을 하나씩 읽어 실행하므로,
# 마지막에 이 스크립트 자신을 갱신하면 실행 중인 파일이 바뀌어 뒷부분이 깨질 수 있다.
# 함수는 통째로 파싱된 뒤 실행되므로 안전하다.
main() {
    cd "$REPO_DIR"
    # root 가 soltrace 소유 저장소에서 git 실행 → dubious ownership 방지.
    # systemd 유닛은 HOME 이 달라 --global 이 안 먹으므로 -c 로 매 명령에 직접 지정.
    GIT="git -c safe.directory=$REPO_DIR"
    $GIT fetch origin
    $GIT checkout "$BRANCH"
    $GIT reset --hard "origin/$BRANCH"
    # root git 으로 root 소유가 된 .git 을 다시 soltrace 로 — WAS 의 git fetch(업데이트 확인)용
    chown -R soltrace:soltrace "$REPO_DIR"

    # ── 1) 의존성 (새 코드보다 먼저 — 옛 코드는 새 패키지가 있어도 문제없다) ──
    cp was/requirements.txt "$DEPLOY_DIR/"
    chown soltrace:soltrace "$DEPLOY_DIR/requirements.txt"
    "$DEPLOY_DIR/venv/bin/pip" install --quiet -r "$DEPLOY_DIR/requirements.txt"

    # ── 2) 스키마 (ADD COLUMN IF NOT EXISTS 형태라 옛 코드와도 호환) ──
    # psql 의 -f 는 postgres 계정으로 파일을 연다 — 저장소가 /root 나 홈 디렉터리(0700) 안에 있으면
    # root 는 읽어도 postgres 는 "Permission denied" 로 실패한다. root 가 읽어 stdin 으로 넘긴다.
    sudo -u postgres psql -d soltrace -f - < "$REPO_DIR/postgres/init.sql"
    # 새로 생긴 테이블/시퀀스 소유권을 soltrace 로 이관 (init.sql 은 postgres 로 적용됨)
    sudo -u postgres psql -d soltrace -tAc "SELECT format('ALTER TABLE public.%I OWNER TO soltrace;', tablename) FROM pg_tables WHERE schemaname='public' UNION ALL SELECT format('ALTER SEQUENCE public.%I OWNER TO soltrace;', sequencename) FROM pg_sequences WHERE schemaname='public'" | sudo -u postgres psql -d soltrace

    # ── 3) nginx ──
    # nginx 렌더링은 공통 라이브러리로 (도메인·인증서 경로 치환이 여기저기 갈라지지 않도록).
    # 라이브러리는 reset --hard 직후의 저장소에서 root 소유 경로로 복사해 두고 그것을 읽는다.
    install -D -m 0644 -o root -g root "$REPO_DIR/scripts/nginx_conf.sh" /usr/local/lib/soltrace/nginx_conf.sh
    # shellcheck source=/dev/null
    source /usr/local/lib/soltrace/nginx_conf.sh
    soltrace_apply_nginx "$REPO_DIR"

    # ── 4) 새 코드 복사 → 곧바로 재시작 (어긋난 창을 최소화) ──
    # 여기서부터 실패해도 재시작은 반드시 수행한다. 이미 새 코드가 깔려 있어
    # 옛 프로세스를 그대로 두면 500 이 이어진다.
    trap 'rc=$?; echo "[$(date "+%F %T")] 배포 중 실패(rc=$rc) — 재시작만 수행"; systemctl restart soltrace-was || true; exit $rc' ERR
    cp -r was/app "$DEPLOY_DIR/"
    cp -r was/static "$DEPLOY_DIR/"
    chown -R soltrace:soltrace "$DEPLOY_DIR/app" "$DEPLOY_DIR/static"
    systemctl restart soltrace-was
    trap - ERR

    # ── 5) 기동 확인 — 30초 안에 응답하지 않으면 실패로 남긴다 ──
    ok=0
    for _ in $(seq 1 30); do
        if curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:8000/; then ok=1; break; fi
        sleep 1
    done
    if [ "$ok" = 1 ]; then
        echo "[$(date '+%F %T')] WAS 기동 확인"
    else
        echo "[$(date '+%F %T')] 경고: 재시작 후 WAS 가 응답하지 않음 — /var/log/soltrace/error.log 확인"
    fi

    # ── 6) 이 스크립트 자신을 최신본으로 (다음 웹 업데이트부터 반영) ──
    # 이 파일은 update_rocky8.sh/install 로만 갱신돼, 배포 스크립트 수정이 서버에
    # 영영 반영되지 않는 구멍이 있었다. 코드는 origin 에서만 받으므로 출처는 동일하다.
    install -m 0755 -o root -g root "$REPO_DIR/scripts/soltrace-selfupdate.sh" /usr/local/sbin/soltrace-selfupdate

    echo "[$(date '+%F %T')] selfupdate 완료"
}

main "$@"

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
set -euo pipefail

REPO_DIR="/opt/soltrace"
DEPLOY_DIR="/opt/soltrace"
BRANCH="main"
LOG="/var/log/soltrace/selfupdate.log"

if [ "${1:-}" != "--run" ]; then
    exec systemd-run \
        --unit="soltrace-selfupdate" \
        --collect \
        --property=Type=oneshot \
        --setenv=PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        /usr/local/sbin/soltrace-selfupdate --run
fi

# ── 실제 배포 (분리 유닛에서 root 로 실행) ──────────────────────────────────
exec >> "$LOG" 2>&1
echo "[$(date '+%F %T')] selfupdate 시작 (branch=$BRANCH)"

cd "$REPO_DIR"
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

cp -r was/app "$DEPLOY_DIR/"
cp -r was/static "$DEPLOY_DIR/"
cp was/requirements.txt "$DEPLOY_DIR/"
chown -R soltrace:soltrace "$DEPLOY_DIR/app" "$DEPLOY_DIR/static" "$DEPLOY_DIR/requirements.txt"

"$DEPLOY_DIR/venv/bin/pip" install --quiet -r "$DEPLOY_DIR/requirements.txt"

sudo -u postgres psql -d soltrace -f "$REPO_DIR/postgres/init.sql"
# 새로 생긴 테이블/시퀀스 소유권을 soltrace 로 이관 (init.sql 은 postgres 로 적용됨)
sudo -u postgres psql -d soltrace -tAc "SELECT format('ALTER TABLE public.%I OWNER TO soltrace;', tablename) FROM pg_tables WHERE schemaname='public' UNION ALL SELECT format('ALTER SEQUENCE public.%I OWNER TO soltrace;', sequencename) FROM pg_sequences WHERE schemaname='public'" | sudo -u postgres psql -d soltrace

sed 's/server was:8000/server 127.0.0.1:8000/' "$REPO_DIR/nginx/nginx.conf" > /etc/nginx/nginx.conf
nginx -t && systemctl reload nginx

systemctl restart soltrace-was
echo "[$(date '+%F %T')] selfupdate 완료"

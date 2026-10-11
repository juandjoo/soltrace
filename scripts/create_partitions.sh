#!/usr/bin/env bash
# ftp_logs 월별 파티션 생성 (당월 + 향후 2개월)
# /etc/cron.d/soltrace-partitions 에 의해 매월 1일 00:30 자동 실행
# 수동 실행: sudo bash scripts/create_partitions.sh

set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "root 권한 필요"; exit 1; }

# shellcheck source=scripts/db_migrate.sh
source "$(dirname "$(readlink -f "$0")")/db_migrate.sh"

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

for offset in 0 1 2; do
    START=$(date -d "$(date +%Y-%m-01) +${offset} months"   +%Y-%m-%d)
    END=$(date   -d "$(date +%Y-%m-01) +$((offset+1)) months" +%Y-%m-%d)
    PART="ftp_logs_$(date -d "$START" +%Y_%m)"

    sudo -u postgres psql -d soltrace -c \
        "CREATE TABLE IF NOT EXISTS ${PART}
         PARTITION OF ftp_logs
         FOR VALUES FROM ('${START}') TO ('${END}');" \
    && echo "$LOG_PREFIX OK: ${PART}" \
    || echo "$LOG_PREFIX ERROR: ${PART}"
done

# postgres 로 만들었으므로 소유자가 postgres 다. 그대로 두면 앱(soltrace)이 이 파티션을
# 직접 읽거나 지우지 못한다 — 설정 > DB 저장소가 500, 디스크 자동 정리 DROP 도 실패.
# (2026-10-01 cron 이 만든 ftp_logs_2026_12 로 실제 발생.) 새 파티션만 이관된다.
soltrace_fix_owner

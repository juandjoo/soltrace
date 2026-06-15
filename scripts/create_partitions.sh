#!/usr/bin/env bash
# ftp_logs 월별 파티션 생성 (당월 + 향후 2개월)
# /etc/cron.d/soltrace-partitions 에 의해 매월 1일 00:30 자동 실행
# 수동 실행: sudo bash scripts/create_partitions.sh

set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "root 권한 필요"; exit 1; }

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

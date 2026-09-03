#!/usr/bin/env bash
# PostgreSQL 16 메모리/WAL 설정을 서버 RAM 비율로 계산해 postgresql.conf 에 적용 (Rocky Linux 8)
#
# install_was_rocky8.sh / update_rocky8.sh 가 호출하며, 수동 실행도 가능:
#   sudo bash scripts/tune_pg_rocky8.sh            # 적용만 (변경 시 재시작 필요 안내)
#   sudo bash scripts/tune_pg_rocky8.sh --restart  # 변경이 있을 때만 PostgreSQL 재시작
#   sudo bash scripts/tune_pg_rocky8.sh --dry-run  # 계산 결과만 출력
#
# 산출 기준 (일반적인 OLTP+집계 혼합 워크로드 권장치):
#   shared_buffers         = RAM 25% (256MB ~ 8GB)
#   effective_cache_size   = RAM 60%
#   work_mem               = 16/32/64MB  (RAM <4G / <8G / ≥8G) — 대시보드 GROUP BY·DISTINCT 디스크 정렬 방지
#   maintenance_work_mem   = 128MB ~ 1GB  — 인덱스 생성·VACUUM
#   max_wal_size           = 1/2/4GB      — bulk import 시 잦은 체크포인트 방지
# 값이 이미 같으면 아무것도 바꾸지 않는다(idempotent).

set -euo pipefail

PG_CONF="${PG_CONF:-/var/lib/pgsql/16/data/postgresql.conf}"
PG_SERVICE="${PG_SERVICE:-postgresql-16}"
DO_RESTART=false; DRY_RUN=false
for a in "$@"; do
    case "$a" in
        --restart) DO_RESTART=true ;;
        --dry-run) DRY_RUN=true ;;
        *) echo "알 수 없는 옵션: $a"; exit 1 ;;
    esac
done

mem_kb=$(awk '/^MemTotal/{print $2}' /proc/meminfo)
mem_mb=$((mem_kb / 1024))

sb=$((mem_mb / 4));      [ "$sb" -gt 8192 ] && sb=8192; [ "$sb" -lt 256 ] && sb=256
ecs=$((mem_mb * 6 / 10)); [ "$ecs" -lt 512 ] && ecs=512
if   [ "$mem_mb" -ge 16384 ]; then wm=64; mwm=1024; mws=4096
elif [ "$mem_mb" -ge 8192  ]; then wm=64; mwm=512;  mws=4096
elif [ "$mem_mb" -ge 4096  ]; then wm=32; mwm=256;  mws=2048
else                                wm=16; mwm=128;  mws=1024
fi

declare -A WANT=(
    [shared_buffers]="${sb}MB"
    [effective_cache_size]="${ecs}MB"
    [work_mem]="${wm}MB"
    [maintenance_work_mem]="${mwm}MB"
    [max_wal_size]="${mws}MB"
    [wal_buffers]="16MB"
    [checkpoint_completion_target]="0.9"
    [random_page_cost]="1.1"
    [log_min_duration_statement]="1000"
)
ORDER=(shared_buffers effective_cache_size work_mem maintenance_work_mem max_wal_size
       wal_buffers checkpoint_completion_target random_page_cost log_min_duration_statement)

echo "서버 RAM: ${mem_mb}MB → 적용값:"
for k in "${ORDER[@]}"; do printf "  %-30s = %s\n" "$k" "${WANT[$k]}"; done
$DRY_RUN && exit 0

[ "$(id -u)" -eq 0 ] || { echo "root 권한 필요"; exit 1; }
[ -f "$PG_CONF" ] || { echo "postgresql.conf 없음: $PG_CONF"; exit 1; }

changed=false
for k in "${ORDER[@]}"; do
    v="${WANT[$k]}"
    # 현재 활성 값(주석 아닌 줄)이 이미 같으면 건너뜀
    if grep -Eq "^[[:space:]]*${k}[[:space:]]*=[[:space:]]*${v}([[:space:]]|#|$)" "$PG_CONF"; then
        continue
    fi
    if grep -Eq "^#?[[:space:]]*${k}[[:space:]]*=" "$PG_CONF"; then
        sed -i -E "s|^#?[[:space:]]*${k}[[:space:]]*=.*|${k} = ${v}|" "$PG_CONF"
    else
        echo "${k} = ${v}" >> "$PG_CONF"
    fi
    changed=true
    echo "  변경: ${k} = ${v}"
done

if ! $changed; then
    echo "변경 없음 (이미 최신 설정)"
    exit 0
fi
if $DO_RESTART; then
    systemctl restart "$PG_SERVICE"
    echo "PostgreSQL 재시작 완료"
else
    echo "※ shared_buffers 등은 재시작 후 반영됩니다: systemctl restart $PG_SERVICE"
fi

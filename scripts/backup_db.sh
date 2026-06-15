#!/usr/bin/env bash
# SolTrace DB 증분 백업 (월별 파티션 기반, 최대 3년치 보관)
# /etc/cron.d/soltrace-backup 에 의해 매일 03:00 자동 실행
# 수동 실행: sudo bash scripts/backup_db.sh
#
# 증분 전략:
#   PostgreSQL 16에는 네이티브 블록 단위 증분 백업이 없으므로(PG17+),
#   ftp_logs 의 월별 파티션 구조를 활용해 증분 백업한다.
#     - 전월 이전 파티션: 더 이상 변하지 않음(immutable) → 백업 파일이 있으면 건너뜀
#     - 당월/전월 파티션: 매번 갱신(데몬 로컬 버퍼로 인한 지연 도착 로그 대비)
#     - default 파티션: 매번 갱신(범위를 벗어난 행이 들어올 수 있음)
#     - 메타(전체 스키마 + 설정/소규모 테이블): 매번 백업, 용량이 작음
#   3년(36개월)을 초과한 백업 파일과 DB 파티션은 자동 삭제한다.
#   (DB 파티션은 해당 백업 파일이 존재할 때만 DROP — 미백업 데이터 손실 방지)

set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "root 권한 필요"; exit 1; }

DB_NAME="soltrace"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/soltrace}"
PART_DIR="$BACKUP_DIR/partitions"
RETENTION_MONTHS="${RETENTION_MONTHS:-36}"      # 3년
PG_DUMP="/usr/pgsql-16/bin/pg_dump"
[ -x "$PG_DUMP" ] || PG_DUMP="pg_dump"          # 경로에 없으면 PATH 사용

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log() { echo "$LOG_PREFIX $*"; }

mkdir -p "$PART_DIR"
chown -R postgres:postgres "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

psql_q() { sudo -u postgres psql -d "$DB_NAME" -tAc "$1"; }
dump()   { sudo -u postgres "$PG_DUMP" -d "$DB_NAME" "$@"; }

CUR_YM=$(date +%Y_%m)
PREV_YM=$(date -d "$(date +%Y-%m-01) -1 month" +%Y_%m)
CUTOFF_YM=$(date -d "$(date +%Y-%m-01) -${RETENTION_MONTHS} months" +%Y_%m)  # 이 달 이전은 3년 초과

# 오래된 DB 파티션도 DROP 할지 (백업 파일이 있는 경우에만 삭제). false 로 끄면 백업만 정리
DROP_OLD_PARTITIONS="${DROP_OLD_PARTITIONS:-true}"

# ── [1/3] 메타 백업 (전체 스키마 + 비파티션 테이블 데이터) ────────────────────
# ftp_logs_* 파티션의 "데이터"만 제외 → 파티션 DDL과 나머지 테이블은 모두 포함
META_FILE="$BACKUP_DIR/meta_$(date +%Y%m%d).sql.gz"
TMP="$META_FILE.tmp"
if dump --exclude-table-data='public.ftp_logs_*' | gzip > "$TMP"; then
    mv -f "$TMP" "$META_FILE"
    log "OK   meta -> $(basename "$META_FILE")"
else
    rm -f "$TMP"
    log "ERROR meta 백업 실패"
fi

# ── [2/3] 파티션 증분 백업 ───────────────────────────────────────────────────
# ftp_logs 의 자식 파티션 목록을 DB에서 직접 조회 (이름 추측 X)
PARTITIONS=$(psql_q \
    "SELECT c.relname
       FROM pg_inherits i
       JOIN pg_class  c ON c.oid = i.inhrelid
       JOIN pg_class  p ON p.oid = i.inhparent
      WHERE p.relname = 'ftp_logs'
      ORDER BY c.relname;")

for PART in $PARTITIONS; do
    OUT="$PART_DIR/${PART}.sql.gz"

    # 당월/전월/default 가 아니고, 이미 백업 파일이 있으면 → 불변 파티션, 건너뜀(증분)
    if [ "$PART" != "ftp_logs_$CUR_YM" ] \
       && [ "$PART" != "ftp_logs_$PREV_YM" ] \
       && [ "$PART" != "ftp_logs_default" ] \
       && [ -f "$OUT" ]; then
        log "SKIP $PART (변경 없음)"
        continue
    fi

    TMP="$OUT.tmp"
    if dump --data-only -t "public.${PART}" | gzip > "$TMP"; then
        mv -f "$TMP" "$OUT"
        log "OK   $PART -> $(basename "$OUT")"
    else
        rm -f "$TMP"
        log "ERROR $PART 백업 실패"
    fi
done

# ── [3/4] 3년 초과 DB 파티션 정리 (DROP) ─────────────────────────────────────
# 백업이 모두 끝난 뒤 실행. 백업 파일이 존재하는 파티션만 DROP 한다.
if [ "$DROP_OLD_PARTITIONS" = "true" ]; then
    for PART in $PARTITIONS; do
        [ "$PART" = "ftp_logs_default" ] && continue
        ym=$(echo "$PART" | sed -nE 's/^ftp_logs_([0-9]{4}_[0-9]{2})$/\1/p')
        [ -n "$ym" ] || continue                # 날짜 형식이 아니면 건너뜀
        [ "$ym" \< "$CUTOFF_YM" ] || continue   # 3년 이내는 보존

        if [ ! -f "$PART_DIR/${PART}.sql.gz" ]; then
            log "WARN $PART 3년 초과지만 백업 파일 없음 → DROP 보류"
            continue
        fi
        if psql_q "DROP TABLE IF EXISTS public.${PART};" >/dev/null; then
            log "DROP $PART (3년 초과 파티션 삭제)"
        else
            log "ERROR $PART DROP 실패"
        fi
    done
fi

# ── [4/4] 보관 기간 초과 백업 파일 삭제 (3년치만 유지) ───────────────────────
# 파티션 백업: 파일명의 YYYY_MM 으로 판별
for f in "$PART_DIR"/ftp_logs_*.sql.gz; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    ym=$(echo "$base" | sed -nE 's/^ftp_logs_([0-9]{4}_[0-9]{2})\.sql\.gz$/\1/p')
    [ -n "$ym" ] || continue                    # default 등 날짜 없는 파일은 보존
    if [ "$ym" \< "$CUTOFF_YM" ]; then
        rm -f "$f"
        log "PRUNE $base (3년 초과)"
    fi
done
# 메타 백업: 수정시각 기준으로 보관 기간 초과분 삭제
find "$BACKUP_DIR" -maxdepth 1 -name 'meta_*.sql.gz' \
     -mtime "+$((RETENTION_MONTHS * 31))" -print -delete \
     | sed "s|^|$LOG_PREFIX PRUNE |" || true

chown -R postgres:postgres "$BACKUP_DIR"
log "백업 완료 (보관: 최근 ${RETENTION_MONTHS}개월, 경로: $BACKUP_DIR)"

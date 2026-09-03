#!/usr/bin/env bash
# DB 스키마 적용 공통 라이브러리 (source 전용, 단독 실행 아님)
#
# 왜 라이브러리인가:
#   install_was_rocky8.sh / update_rocky8.sh / soltrace-selfupdate.sh 세 곳이 모두
#   init.sql 을 적용하고 소유권을 이관한다. 규칙이 갈라지면 배포 경로마다 다르게 동작한다.
#
# 왜 이런 모양인가 (2026-09-03 장애):
#   ALTER TABLE 은 ACCESS EXCLUSIVE 락이 필요하다. 라이브 ingest 가 ftp_logs 를 잡고 있으면
#   DDL 이 락 큐에서 대기하고, 대기 중인 DDL 뒤로 신규 쿼리가 전부 막힌다 → 커넥션 풀 고갈
#   → 하트비트까지 500. 실제로 스키마 단계가 30분을 끌어 그동안 서비스가 죽었다.
#   그래서 (1) lock_timeout 으로 빨리 포기하고 재시도하고, (2) 소유권 이관은 실제로
#   소유자가 다른 객체에만 건다(정상 상태에서는 0건 → DDL 자체가 없다).
#
# 사용:
#   source scripts/db_migrate.sh
#   soltrace_apply_schema <repo_dir>
#   soltrace_build_mkdir_index        # 재시작 후 best-effort

SOLTRACE_DB="${SOLTRACE_DB:-soltrace}"
SOLTRACE_LOCK_TIMEOUT="${SOLTRACE_LOCK_TIMEOUT:-5s}"   # DDL 이 이 시간 안에 락을 못 잡으면 포기
SOLTRACE_SCHEMA_RETRIES="${SOLTRACE_SCHEMA_RETRIES:-3}"
SOLTRACE_SCHEMA_RETRY_WAIT="${SOLTRACE_SCHEMA_RETRY_WAIT:-20}"

_soltrace_psql() { sudo -u postgres psql -d "$SOLTRACE_DB" "$@"; }

# init.sql 적용. 락 대기로 취소된 문장이 있으면 파일 전체를 재시도한다
# (init.sql 은 IF NOT EXISTS 형태라 몇 번을 돌려도 같은 결과).
soltrace_apply_schema() {
    local repo_dir="$1" attempt=1 out
    while :; do
        out=$({ echo "SET lock_timeout = '$SOLTRACE_LOCK_TIMEOUT';"; cat "$repo_dir/postgres/init.sql"; } \
              | _soltrace_psql 2>&1)
        echo "$out"
        if ! echo "$out" | grep -qi "lock timeout"; then
            break
        fi
        if [ "$attempt" -ge "$SOLTRACE_SCHEMA_RETRIES" ]; then
            echo "WARN: 스키마 적용이 락 대기로 취소됨 (${attempt}회 시도). 트래픽이 적을 때 재시도 필요."
            break
        fi
        echo "스키마 적용이 락 대기로 취소됨 — ${attempt}회차, ${SOLTRACE_SCHEMA_RETRY_WAIT}초 뒤 재시도"
        attempt=$((attempt + 1))
        sleep "$SOLTRACE_SCHEMA_RETRY_WAIT"
    done
    soltrace_fix_owner
}

# 앱(soltrace)이 런타임에 파티션을 만들려면 부모 테이블 소유자여야 한다.
# 소유자가 이미 soltrace 인 객체는 건너뛴다 — 매 배포마다 전체 테이블에 ALTER 를 걸면
# 그 자체가 락 폭풍이 된다(장애 당시 29개 테이블에 ALTER TABLE 이 걸렸다).
soltrace_fix_owner() {
    local sql
    sql=$(_soltrace_psql -tAc "
        SELECT format('ALTER TABLE public.%I OWNER TO soltrace;', tablename)
          FROM pg_tables
         WHERE schemaname = 'public' AND tableowner <> 'soltrace'
        UNION ALL
        SELECT format('ALTER SEQUENCE public.%I OWNER TO soltrace;', sequencename)
          FROM pg_sequences
         WHERE schemaname = 'public' AND sequenceowner <> 'soltrace'")
    if [ -z "$sql" ]; then
        echo "소유권 이관: 변경할 객체 없음"
        return 0
    fi
    echo "소유권 이관: $(echo "$sql" | grep -c .)건"
    printf 'SET lock_timeout = %s;\n%s\n' "'$SOLTRACE_LOCK_TIMEOUT'" "$sql" | _soltrace_psql
}

# ftp_logs 의 mkdir 부분 인덱스를 파티션별로 CONCURRENTLY 생성해 부모에 붙인다.
#
# 부모 인덱스는 init.sql 이 ON ONLY 로 만든다(스캔·락 없이 즉시, invalid 상태).
# 파티션 인덱스를 CONCURRENTLY 로 만들어 ATTACH 하면 쓰기를 막지 않고, 모든 파티션이
# 붙는 순간 부모 인덱스가 자동으로 valid 가 된다. 이후 새로 생기는 파티션은
# 부모 인덱스를 따라 자동 생성된다(빈 파티션이라 즉시).
# 파티션 수만큼 시간이 걸릴 수 있어 배포에서는 재시작 뒤 best-effort 로 부른다.
soltrace_build_mkdir_index() {
    # 이미 부모 인덱스에 붙은 파티션은 건너뛴다. 새로 생긴 파티션은 부모를 따라
    # 자동 생성돼 이름이 다르므로(<part>_device_id_file_path_log_time_idx),
    # 이름이 아니라 "붙어 있는가"로 판단해야 중복 인덱스가 생기지 않는다.
    local part idx rc=0
    for part in $(_soltrace_psql -tAc "
            SELECT c.relname
              FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE p.relname = 'ftp_logs'
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_index x
                     JOIN pg_inherits ii ON ii.inhrelid = x.indexrelid
                    WHERE x.indrelid = c.oid
                      AND ii.inhparent = (SELECT oid FROM pg_class
                                           WHERE relname = 'idx_ftp_logs_mkdir_path'))
             ORDER BY c.relname"); do
        idx="${part}_mkdir_path_idx"
        if ! _soltrace_psql -q -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS \"$idx\"
                 ON public.\"$part\" (device_id, file_path, log_time) WHERE action = 'mkdir'"; then
            # 실패하면 invalid 인덱스가 남아 이후 시도를 막는다 — 지우고 다음 파티션으로.
            _soltrace_psql -q -c "DROP INDEX IF EXISTS public.\"$idx\"" || true
            echo "WARN: $part 인덱스 생성 실패 — 건너뜀"
            rc=1
            continue
        fi
        # 이미 붙어 있으면 에러가 나므로 무시한다 (재실행 안전).
        _soltrace_psql -q -c "ALTER INDEX idx_ftp_logs_mkdir_path
                 ATTACH PARTITION public.\"$idx\"" 2>/dev/null || true
    done
    return $rc
}

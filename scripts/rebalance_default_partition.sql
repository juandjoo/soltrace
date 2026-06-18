-- ftp_logs_default 재배치: 과거 월 파티션으로 데이터 이동
-- 실행 전 확인:
--   SELECT date_trunc('month', log_time)::date AS month, COUNT(*) FROM ftp_logs_default GROUP BY 1 ORDER BY 1;
-- 실행 방법:
--   docker compose exec -T db psql -U soltrace -d soltrace -f /tmp/rebalance_default_partition.sql

DO $$
DECLARE
    r      RECORD;
    s      DATE;
    e      DATE;
    pname  TEXT;
    moved  BIGINT;
BEGIN
    -- 이동 대상: 이번 달 이전 데이터만 (현재 월 데이터는 건드리지 않음)
    FOR r IN
        SELECT DISTINCT date_trunc('month', log_time)::date AS ms
        FROM ftp_logs_default
        WHERE log_time < date_trunc('month', now())
        ORDER BY 1
    LOOP
        s     := r.ms;
        e     := s + interval '1 month';
        pname := 'ftp_logs_' || to_char(s, 'YYYY_MM');

        -- 파티션이 없으면 생성
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = pname) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF ftp_logs FOR VALUES FROM (%L) TO (%L)',
                pname, s, e
            );
            RAISE NOTICE 'Created partition: %', pname;
        END IF;

        -- ftp_logs_default → 월 파티션으로 이동 (중복은 무시)
        EXECUTE format(
            'WITH d AS (
                DELETE FROM ftp_logs_default
                WHERE log_time >= %L AND log_time < %L
                RETURNING *
            )
            INSERT INTO ftp_logs SELECT * FROM d ON CONFLICT DO NOTHING',
            s, e
        );
        GET DIAGNOSTICS moved = ROW_COUNT;
        RAISE NOTICE 'Moved % rows → %', moved, pname;
    END LOOP;

    RAISE NOTICE 'Done.';
END $$;

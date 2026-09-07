-- ftp_logs_default 재배치: default 파티션에 남은 과거 데이터를 월 파티션으로 옮긴다.
--
-- 언제 필요한가:
--   월 파티션이 없는 동안 들어온 로그는 default 파티션에 쌓인다. 그 상태가 되면
--   (1) 해당 월 파티션을 새로 만들 수 없고(default 의 행이 새 파티션 제약을 위배),
--   (2) 보존 기간 정리·월별 삭제 대상에서도 빠진다.
--   설정 > DB 저장소 화면의 "default 파티션" 항목이 0이 아니면 이 스크립트를 돌린다.
--
-- 실행 전 확인:
--   SELECT date_trunc('month', log_time)::date AS month, COUNT(*)
--     FROM ftp_logs_default GROUP BY 1 ORDER BY 1;
--
-- 실행 방법 (운영: Rocky8 + 로컬 PostgreSQL):
--   sudo -u postgres psql -d soltrace -f /opt/soltrace/scripts/rebalance_default_partition.sql
--
-- 안전성:
--   * 이번 달 이전 데이터만 건드린다(수집 중인 당월은 그대로 둔다).
--   * 5만 행마다 커밋한다 — 중간에 끊겨도 옮긴 만큼은 남고, 다시 실행하면 이어서 한다.
--   * 데이터를 옮기는 동안에는 부모 테이블(ftp_logs)에 락을 걸지 않는다. 옮길 곳을
--     '독립 테이블'로 만들어 채운 뒤 마지막에 ATTACH 로 붙이므로, 라이브 수집을 막는
--     ACCESS EXCLUSIVE 락은 붙이는 순간에만 잡힌다(2026-09-03 배포 장애의 교훈).
--   * lock_timeout 으로 락 대기가 길어지면 스스로 포기한다. 그 경우 트래픽이 적을 때
--     다시 실행하면 된다(재실행 안전).

SET lock_timeout = '5s';

DO $do$
DECLARE
    CHUNK   CONSTANT INT := 50000;   -- 한 트랜잭션에서 옮길 행 수
    months  DATE[];
    s       DATE;
    e       DATE;
    pname   TEXT;
    owner   TEXT;
    moved   BIGINT;
    total   BIGINT;
    attached BOOLEAN;
BEGIN
    SELECT pg_get_userbyid(relowner) INTO owner
      FROM pg_class WHERE relname = 'ftp_logs' AND relnamespace = 'public'::regnamespace;

    -- 대상 월을 먼저 배열로 뽑는다 — 커서 루프 안에서는 COMMIT 할 수 없다.
    -- (1) default 에 남은 과거 월 + (2) 지난 실행이 붙이기 전에 끊겨 남은 테이블.
    --     (2)를 빼면 default 는 비었는데 데이터는 조회에 안 잡히는 상태로 굳는다.
    SELECT array_agg(DISTINCT m ORDER BY m) INTO months FROM (
        SELECT date_trunc('month', log_time)::date AS m
          FROM ftp_logs_default
         WHERE log_time < date_trunc('month', now())
        UNION
        SELECT to_date(substring(c.relname FROM 10), 'YYYY_MM')
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'r'
           AND c.relname ~ '^ftp_logs_[0-9]{4}_[0-9]{2}$'
           AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = c.oid)
    ) t;

    IF months IS NULL THEN
        RAISE NOTICE '옮길 데이터가 없습니다 (default 파티션이 비었거나 당월 데이터만 있음).';
        RETURN;
    END IF;

    FOREACH s IN ARRAY months LOOP
        e     := (s + INTERVAL '1 month')::date;
        pname := 'ftp_logs_' || to_char(s, 'YYYY_MM');
        total := 0;

        SELECT EXISTS (
            SELECT 1 FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE p.relname = 'ftp_logs' AND c.relname = pname
        ) INTO attached;

        IF attached THEN
            -- 정상적으로는 올 수 없는 상태다. PostgreSQL 은 이미 파티션이 맡은 범위의 행을
            -- default 에 넣지 못하게 막으므로, 여기 걸린다면 그 파티션의 범위가 '한 달'이
            -- 아니라는 뜻이다(수동 생성 등). 그때 부모로 다시 밀어 넣으면 일부가 default 로
            -- 되돌아와 끝나지 않으므로 건드리지 않고 사람이 판단하게 남긴다.
            RAISE WARNING '%: 이미 파티션이 있는데 default 에 이 달 행이 남아 있습니다. '
                          '파티션 범위를 확인하세요 (psql 에서 \d+ ftp_logs). 건너뜁니다.', pname;
            CONTINUE;
        END IF;

        -- (1) 옮길 곳을 '독립 테이블'로 만든다. 아직 파티션이 아니므로 부모에 락이 없다.
        --     인덱스까지 미리 만들어 두면 ATTACH 때 인덱스를 새로 만드느라 락을 오래
        --     잡는 일이 없다. IDENTITY 는 일부러 빼서 원래 id 를 그대로 넣는다.
        IF to_regclass('public.' || quote_ident(pname)) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I (LIKE ftp_logs INCLUDING DEFAULTS INCLUDING CONSTRAINTS '
                'INCLUDING INDEXES INCLUDING STORAGE INCLUDING COMMENTS)', pname);
            EXECUTE format('ALTER TABLE %I OWNER TO %I', pname, owner);
            RAISE NOTICE '% 준비 (아직 파티션 아님)', pname;
        END IF;

        -- 파티션 범위와 같은 CHECK → ATTACH 가 전수 검사를 건너뛴다(락 시간 최소화).
        -- 중단 후 재실행이면 테이블이 이미 있을 수 있으므로 '없으면 추가'로 둔다.
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = format('public.%I', pname)::regclass
               AND conname  = pname || '_range_chk'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I CHECK (log_time >= %L AND log_time < %L)',
                pname, pname || '_range_chk', s, e);
        END IF;

        -- (2) default → 독립 테이블. 청크마다 커밋하므로 중단돼도 이어서 재실행 가능.
        LOOP
            EXECUTE format($q$
                WITH d AS (
                    DELETE FROM ftp_logs_default
                     WHERE ctid IN (SELECT ctid FROM ftp_logs_default
                                     WHERE log_time >= %L AND log_time < %L
                                     LIMIT %s)
                    RETURNING *
                )
                INSERT INTO %I SELECT * FROM d ON CONFLICT DO NOTHING
            $q$, s, e, CHUNK, pname);
            GET DIAGNOSTICS moved = ROW_COUNT;
            total := total + moved;
            COMMIT;
            EXIT WHEN moved = 0;
        END LOOP;

        -- (3) 붙인다. 부모 락은 이 순간에만 잡히고, 붙고 나면 범위 CHECK 는 파티션
        --     제약이 대신하므로 지운다.
        EXECUTE format('ALTER TABLE ftp_logs ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
                       pname, s, e);
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', pname, pname || '_range_chk');
        COMMIT;
        -- 통계를 바로 갱신한다 — 설정 > DB 저장소의 행 수(추정)가 곧바로 맞게 보인다.
        EXECUTE format('ANALYZE %I', pname);
        RAISE NOTICE '%: % 행 이동 후 파티션으로 붙임', pname, total;
    END LOOP;

    RAISE NOTICE '완료.';
END
$do$;

-- 붙이기 전에 중단됐다면 여기에 이름이 뜬다 → 스크립트를 다시 실행하면 마저 붙인다.
-- (붙기 전까지 그 데이터는 조회에 잡히지 않는다.)
SELECT c.relname AS "붙지 않은 테이블", pg_size_pretty(pg_total_relation_size(c.oid)) AS "크기"
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND c.relname ~ '^ftp_logs_[0-9]{4}_[0-9]{2}$'
   AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = c.oid)
 ORDER BY 1;

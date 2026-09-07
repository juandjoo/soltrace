-- 과거 로그의 '클라이언트 오류' 재분류
--
-- 무엇을 하나:
--   파일명 없이 들어온 전송 요청(`STOR /vod//` 처럼 경로가 '/' 로 끝나는 것)은 서버가
--   멀쩡해도 늘 실패한다. 데몬 1.1.6 부터는 이런 건을 action='client_error' 로 따로 기록해
--   전송 실패율·서비스 영향도·알림에서 빼지만, 그 이전에 쌓인 행은 upload/download + fail
--   로 남아 있어 실패율을 계속 부풀린다. 이 스크립트가 그 과거분을 같은 기준으로 옮긴다.
--
--   1) action 을 'client_error' 로 바꾼다
--   2) 겹친 슬래시를 하나로 줄인다 ('/vod//' -> '/vod/') — 데몬 1.1.5+ 와 같은 표기
--   3) row_hash 를 새 값으로 다시 계산한다 (app/routers/ingest.py _row_hash 와 같은 식)
--
--   응답 코드(500/501/502/504)로 판정하는 '명령 문법 오류'는 ftp_logs 에 코드가 남지 않아
--   과거분을 가려낼 수 없다. 경로 규칙만 소급한다.
--
-- 실행 전 확인 (얼마나 바뀌는지):
--   SELECT date_trunc('month', log_time)::date AS month, COUNT(*)
--     FROM ftp_logs
--    WHERE action IN ('upload','download') AND status = 'fail' AND file_path LIKE '%/'
--    GROUP BY 1 ORDER BY 1;
--
--   SELECT file_path, COUNT(*) FROM ftp_logs
--    WHERE action IN ('upload','download') AND status = 'fail' AND file_path LIKE '%/'
--    GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
--
-- 실행 방법 (운영: Rocky8 + 로컬 PostgreSQL):
--   sudo -u postgres psql -d soltrace -f /opt/soltrace/scripts/reclassify_client_error.sql
--
--   기간을 좁히면 파티션 가지치기로 훨씬 빠르다. 아래 p_since / p_until 을 고친다.
--
-- 안전성:
--   * 행을 지우지 않는다. action / file_path / row_hash 세 컬럼만 바꾼다.
--   * 1만 행마다 커밋한다 — 중간에 끊겨도 바꾼 만큼은 남고, 다시 실행하면 이어서 한다
--     (바뀐 행은 action 이 달라져 다음 실행의 대상에서 빠진다).
--   * row_hash 유니크 인덱스(device_id, log_time, row_hash)와 부딪히는 행은 건드리지 않고
--     건너뛴다. 같은 사건이 이미 client_error 로 들어와 있다는 뜻이라 바꿀 필요가 없다.
--     건너뛴 수는 마지막에 알려준다.
--   * lock_timeout 으로 락 대기가 길어지면 스스로 포기한다. 재실행 안전.
--   * 파티션 키(log_time)는 건드리지 않으므로 행이 파티션 사이를 옮겨 다니지 않는다.

SET lock_timeout = '5s';

DO $do$
DECLARE
    CHUNK    CONSTANT INT  := 10000;                     -- 한 트랜잭션에서 바꿀 행 수
    p_since  CONSTANT TIMESTAMPTZ := '2000-01-01';       -- 필요하면 좁힌다
    p_until  CONSTANT TIMESTAMPTZ := NOW();
    last_id  BIGINT := 0;
    cand_max BIGINT;
    cand_cnt BIGINT;
    upd_cnt  BIGINT;
    moved    BIGINT := 0;
    skipped  BIGINT := 0;
BEGIN
    LOOP
        WITH cand AS MATERIALIZED (
            SELECT id, log_time, device_id, username, file_path, file_size, session_id, client_ip
              FROM ftp_logs
             WHERE log_time >= p_since AND log_time < p_until
               AND action IN ('upload', 'download')
               AND status = 'fail'
               AND file_path LIKE '%/'          -- 파일명이 없다
               AND id > last_id
             ORDER BY id
             LIMIT CHUNK
        ), fixed AS (
            SELECT c.*,
                   regexp_replace(c.file_path, '/{2,}', '/', 'g') AS new_path
              FROM cand c
        ), hashed AS (
            SELECT f.*,
                   md5(
                       f.device_id::text || '|' ||
                       to_char(f.log_time AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '|' ||
                       coalesce(f.username, '') || '|' || 'client_error' || '|' ||
                       coalesce(f.new_path, '') || '|' ||
                       coalesce(f.file_size::text, '0') || '|' ||
                       coalesce(f.session_id, '') || '|' ||
                       coalesce(f.client_ip, '')
                   ) AS new_hash
              FROM fixed f
        ), upd AS (
            UPDATE ftp_logs t
               SET action    = 'client_error',
                   file_path = h.new_path,
                   row_hash  = h.new_hash
              FROM hashed h
             WHERE t.id = h.id AND t.log_time = h.log_time
               -- 같은 사건이 이미 client_error 로 들어와 있으면 그대로 둔다 (유니크 충돌 회피)
               AND NOT EXISTS (
                   SELECT 1 FROM ftp_logs x
                    WHERE x.device_id = h.device_id
                      AND x.log_time  = h.log_time
                      AND x.row_hash  = h.new_hash
               )
            RETURNING 1
        )
        SELECT (SELECT max(id) FROM cand),
               (SELECT count(*) FROM cand),
               (SELECT count(*) FROM upd)
          INTO cand_max, cand_cnt, upd_cnt;

        EXIT WHEN cand_cnt = 0;

        moved   := moved + upd_cnt;
        skipped := skipped + (cand_cnt - upd_cnt);
        last_id := cand_max;          -- 건너뛴 행 때문에 같은 자리를 맴돌지 않게 한다
        COMMIT;

        RAISE NOTICE '진행: 옮김 % / 건너뜀 % (마지막 id %)', moved, skipped, last_id;
    END LOOP;

    RAISE NOTICE '완료: client_error 로 옮긴 행 %, 이미 있어 건너뛴 행 %', moved, skipped;
END
$do$;

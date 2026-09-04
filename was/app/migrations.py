"""기동 시 실행하는 스키마 마이그레이션.

`Base.metadata.create_all` 이 못 만드는 변경(컬럼 rename, 부분/GIN 인덱스, 데이터 백필)을
idempotent 한 DDL 로 적용한다. 배포 스크립트가 아니라 여기서 도는 이유는, 워커가 새 코드로
기동하는 시점과 스키마가 갖춰지는 시점을 어긋나지 않게 묶기 위해서다.

새 마이그레이션은 반드시 "이미 적용됐으면 아무 것도 하지 않는" 형태로 쓴다
(IF NOT EXISTS / information_schema·pg_indexes 확인).
"""
from sqlalchemy import text


def run_migrations(conn):
    """스키마 변경이 필요한 마이그레이션을 idempotent하게 실행한다."""
    # groups.auth → groups.application (rename)
    conn.execute(text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='groups' AND column_name='auth')
             AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='groups' AND column_name='application')
          THEN ALTER TABLE groups RENAME COLUMN auth TO application; END IF;
        END $$
    """))
    # client_ip GIN trigram 인덱스 — ILIKE '%...%' 검색 성능
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ftp_logs_client_ip_trgm')
          THEN CREATE INDEX idx_ftp_logs_client_ip_trgm ON ftp_logs USING gin (client_ip gin_trgm_ops); END IF;
        END $$
    """))
    # file_path GIN trigram 인덱스 — 파일명/경로 부분 검색 성능
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ftp_logs_file_path_trgm')
          THEN CREATE INDEX idx_ftp_logs_file_path_trgm ON ftp_logs USING gin (file_path gin_trgm_ops); END IF;
        END $$
    """))
    # device_groups.group_id 인덱스 — PK가 (device_id, group_id) 순서라 group_id 단독 조회 불가
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_device_groups_group_id')
          THEN CREATE INDEX idx_device_groups_group_id ON device_groups (group_id); END IF;
        END $$
    """))
    # service_metrics (device_id, bucket) 복합 인덱스 — 장비별 시계열 쿼리
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_service_metrics_device_bucket')
          THEN CREATE INDEX idx_service_metrics_device_bucket ON service_metrics (device_id, bucket); END IF;
        END $$
    """))
    # service_alerts (device_id, created_at) 복합 인덱스 — 장비별 알림 조회
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_service_alerts_device_created')
          THEN CREATE INDEX idx_service_alerts_device_created ON service_alerts (device_id, created_at); END IF;
        END $$
    """))
    # service_alerts.notified 부분 인덱스 — 미발송 알림 polling
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_service_alerts_notified')
          THEN CREATE INDEX idx_service_alerts_notified ON service_alerts (notified) WHERE notified = FALSE; END IF;
        END $$
    """))
    # users 로그인 잠금 컬럼 — 연속 실패 횟수/잠금 해제 시각/마지막 로그인
    conn.execute(text("""
        ALTER TABLE users
          ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS locked_until    TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS last_login_at   TIMESTAMPTZ
    """))
    # ftp_logs.row_hash 컬럼 — 재전송 중복 방지용 MD5 식별키
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='ftp_logs' AND column_name='row_hash')
          THEN ALTER TABLE ftp_logs ADD COLUMN row_hash VARCHAR(32); END IF;
        END $$
    """))
    # 인덱스가 없을 때만 한 번 실행: 중복 제거 → 백필 → 유니크 인덱스 생성
    # 순서가 중요: 기존 중복 행을 먼저 제거해야 백필 후 인덱스 생성이 실패하지 않음
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ftp_logs_dedup') THEN
            -- 1) 기존 중복 행 제거: 동일 내용이면 id 낮은 쪽(먼저 들어온 행) 유지
            DELETE FROM ftp_logs
            WHERE id IN (
              SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                  PARTITION BY
                    device_id,
                    to_char(log_time AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
                    coalesce(username, ''), action,
                    coalesce(file_path, ''), coalesce(file_size::text, '0'),
                    coalesce(session_id, ''), coalesce(client_ip, '')
                  ORDER BY id
                ) AS rn
                FROM ftp_logs
                WHERE log_time >= now() - interval '90 days'
              ) t
              WHERE rn > 1
            );
            -- 2) 백필: Python _row_hash()와 동일한 필드·순서로 MD5 계산
            UPDATE ftp_logs SET row_hash = md5(
              device_id::text || '|' ||
              to_char(log_time AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '|' ||
              coalesce(username, '') || '|' || action || '|' ||
              coalesce(file_path, '') || '|' ||
              coalesce(file_size::text, '0') || '|' ||
              coalesce(session_id, '') || '|' ||
              coalesce(client_ip, '')
            ) WHERE row_hash IS NULL AND log_time >= now() - interval '90 days';
            -- 3) 유니크 인덱스 생성 — 파티션 키(log_time) 포함으로 파티셔닝 테이블 호환
            CREATE UNIQUE INDEX idx_ftp_logs_dedup ON ftp_logs (device_id, log_time, row_hash);
          END IF;
        END $$
    """))

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- ILIKE '%...%' 인덱스 지원

-- 전역 설정 키-값 (관리자 비밀번호 해시, 텔코 정보 등)
CREATE TABLE IF NOT EXISTS app_config (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 웹 로그인 계정 (admin은 app_config로 부트스트랩, 고객사 계정만 여기에 저장)
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          VARCHAR(16) NOT NULL DEFAULT 'customer'
                  CHECK (role IN ('admin','customer')),
    customer      TEXT,              -- role=customer일 때 groups.customer 와 매칭
    allowed_ips   TEXT,              -- 계정별 허용 IP/CIDR (비어있으면 제한 없음)
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 계정별 API 키 (조회 전용 토큰).
-- user_id NULL = 관리자(admin) 키. 고객 계정 키는 users 를 참조하며 계정 삭제 시 함께 사라진다.
-- 원본 키는 저장하지 않고 SHA-256 해시만 보관하며, 목록 표시는 key_prefix 로 한다.
CREATE TABLE IF NOT EXISTS api_keys (
    id           SERIAL PRIMARY KEY,
    user_id      INT REFERENCES users(id) ON DELETE CASCADE,
    label        VARCHAR(100) NOT NULL DEFAULT '',
    key_prefix   VARCHAR(20)  NOT NULL,        -- 화면 식별용 앞부분 (예: slt_1a2b3c…)
    key_hash     VARCHAR(64)  UNIQUE NOT NULL, -- sha256(원본키) hex
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at   TIMESTAMPTZ,                  -- NULL = 무기한
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS devices (
    id SERIAL PRIMARY KEY,
    hostname VARCHAR(255) NOT NULL,
    ip_address VARCHAR(45),
    device_key VARCHAR(64) UNIQUE NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'disabled')),
    os_info TEXT,
    proftpd_version VARCHAR(50),
    daemon_version VARCHAR(20),
    last_heartbeat TIMESTAMPTZ,
    -- 데몬 상태
    daemon_status VARCHAR(20) DEFAULT 'unknown',
    last_send_time TIMESTAMPTZ,
    buffer_lines INT DEFAULT 0,
    queue_size INT DEFAULT 0,
    consecutive_failures INT DEFAULT 0,
    error_message TEXT,
    cpu_percent FLOAT,
    mem_mb FLOAT,
    disk_free_gb FLOAT,
    daemon_uptime INT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 기존 DB 마이그레이션 (컬럼 없는 경우에만 추가)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='devices' AND column_name='daemon_status') THEN
        ALTER TABLE devices
            ADD COLUMN daemon_status VARCHAR(20) DEFAULT 'unknown',
            ADD COLUMN last_send_time TIMESTAMPTZ,
            ADD COLUMN buffer_lines INT DEFAULT 0,
            ADD COLUMN queue_size INT DEFAULT 0,
            ADD COLUMN consecutive_failures INT DEFAULT 0,
            ADD COLUMN error_message TEXT,
            ADD COLUMN cpu_percent FLOAT,
            ADD COLUMN mem_mb FLOAT,
            ADD COLUMN disk_free_gb FLOAT,
            ADD COLUMN daemon_uptime INT;
    END IF;
END $$;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS kernel_version VARCHAR(100);
ALTER TABLE devices ADD COLUMN IF NOT EXISTS update_requested BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    telco VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- 기존 DB 마이그레이션 (CREATE TABLE IF NOT EXISTS 로는 스키마가 갱신되지 않음)
ALTER TABLE groups ADD COLUMN IF NOT EXISTS telco VARCHAR(100);
ALTER TABLE groups DROP COLUMN IF EXISTS group_type;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS customer TEXT;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS upload_domains TEXT;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS auth TEXT;

-- 통신사 목록
CREATE TABLE IF NOT EXISTS telcos (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS device_groups (
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    group_id INT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (device_id, group_id)  -- device_id 선행 조회용
);

CREATE TABLE IF NOT EXISTS ftp_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    log_time TIMESTAMPTZ NOT NULL,
    client_ip VARCHAR(45),
    username VARCHAR(255),
    action VARCHAR(20) NOT NULL CHECK (action IN ('upload', 'download', 'delete', 'rename', 'login', 'logout', 'mkdir', 'rmdir', 'cwd_fail')),
    file_path TEXT,
    file_size BIGINT DEFAULT 0,
    transfer_time FLOAT DEFAULT 0,
    transfer_type VARCHAR(10),
    status VARCHAR(10) DEFAULT 'success' CHECK (status IN ('success', 'fail')),
    session_id VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, log_time)
) PARTITION BY RANGE (log_time);

-- DEFAULT 파티션: 매칭 파티션 없는 행 임시 보관 (신규 월 파티션 생성 전 안전망)
-- 아래 월 파티션 루프가 default 의 데이터 존재 여부를 검사하므로 먼저 만든다.
CREATE TABLE IF NOT EXISTS ftp_logs_default PARTITION OF ftp_logs DEFAULT;

-- 초기 파티션 생성 (과거 36개월 + 당월 + 향후 2개월) — 이미 존재하면 건너뜀
-- 과거 범위는 backup_db.sh RETENTION_MONTHS(36) 및 was/app/main.py PARTITION_PAST_MONTHS 와 동일.
-- default 파티션에 해당 월 데이터가 있으면 CREATE 가 실패(CheckViolation)하므로 그 달은 건너뛴다
-- → 설정 > DB 저장소 확인 후 scripts/rebalance_default_partition.sql 로 재배치.
DO $$
DECLARE
    base_date DATE := date_trunc('month', NOW())::DATE;
    i         INT;
    s         DATE;
    e         DATE;
    pname     TEXT;
    has_data  BOOLEAN;
BEGIN
    FOR i IN -36..2 LOOP
        s := (base_date + (i || ' months')::INTERVAL)::DATE;
        e := (s + '1 month'::INTERVAL)::DATE;
        pname := 'ftp_logs_' || to_char(s, 'YYYY_MM');
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = pname AND n.nspname = 'public'
        ) THEN
            EXECUTE format(
                'SELECT EXISTS(SELECT 1 FROM ftp_logs_default WHERE log_time >= %L AND log_time < %L)',
                s, e
            ) INTO has_data;
            IF has_data THEN
                RAISE WARNING 'Partition % skipped: ftp_logs_default has rows for this month (run rebalance_default_partition.sql)', pname;
                CONTINUE;
            END IF;
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF ftp_logs FOR VALUES FROM (%L) TO (%L)',
                pname, s, e
            );
            RAISE NOTICE 'Partition created: %', pname;
        END IF;
    END LOOP;
END $$;

-- ────────────────────────────────────────────────────────────────────────────
-- ftp_logs 인덱스
-- ────────────────────────────────────────────────────────────────────────────

-- [1] 기간 범위 조회 (대시보드 base filter, 로그 목록 전체 기간 조회)
--     WHERE log_time >= ? AND log_time <= ?  ORDER BY log_time DESC
CREATE INDEX IF NOT EXISTS idx_ftp_logs_log_time
    ON ftp_logs(log_time DESC);

-- [2] 장비별 기간 조회 (장비 선택 + 기간 필터 - 가장 빈번한 복합 조건)
--     WHERE device_id = ? AND log_time >= ?  ORDER BY log_time DESC
CREATE INDEX IF NOT EXISTS idx_ftp_logs_device_time
    ON ftp_logs(device_id, log_time DESC);

-- [3] username ILIKE '%...%' 검색 (pg_trgm GIN - B-tree로는 부분일치 불가)
--     WHERE username ILIKE '%keyword%'
CREATE INDEX IF NOT EXISTS idx_ftp_logs_username_trgm
    ON ftp_logs USING GIN (username gin_trgm_ops);

-- [4] client_ip ILIKE '%...%' 검색 (IP 부분 일치)
CREATE INDEX IF NOT EXISTS idx_ftp_logs_client_ip_trgm
    ON ftp_logs USING GIN (client_ip gin_trgm_ops);

-- [5] CWD 실패 판정용 — 실패 직후 같은 경로가 생성됐는지(존재 확인) 확인하는 anti-join.
--     WHERE action='mkdir' AND device_id=? AND file_path=? AND log_time BETWEEN ...
--     mkdir 만 담는 부분 인덱스라 크기가 작다 (service_monitor.cwd_real_fail_sql).
--
--     ON ONLY 인 이유: 여기서 파티션까지 한 번에 만들면 파티션마다 쓰기를 막고 전체를
--     스캔한다(라이브 ingest 가 멈춘다). 부모에만 만들어 두면 스캔 없이 즉시 끝나고,
--     실제 파티션 인덱스는 배포 스크립트가 CONCURRENTLY 로 만들어 붙인다
--     (scripts/db_migrate.sh 의 soltrace_build_mkdir_index).
--     모든 파티션이 붙으면 이 부모 인덱스는 자동으로 valid 가 되고, 이후 새로 생기는
--     파티션은 부모를 따라 자동 생성된다(빈 파티션이라 즉시).
CREATE INDEX IF NOT EXISTS idx_ftp_logs_mkdir_path
    ON ONLY ftp_logs (device_id, file_path, log_time)
    WHERE action = 'mkdir';

-- ────────────────────────────────────────────────────────────────────────────
-- device_groups 인덱스
-- ────────────────────────────────────────────────────────────────────────────

-- [4] 그룹별 장비 역방향 조회 (PK는 device_id 선행이므로 group_id 단독 조회 불가)
--     WHERE group_id = ?  (그룹에 속한 장비 목록, device_count 집계)
CREATE INDEX IF NOT EXISTS idx_device_groups_group_id
    ON device_groups(group_id);

-- ────────────────────────────────────────────────────────────────────────────
-- 제거된 인덱스 (이유)
-- ────────────────────────────────────────────────────────────────────────────
-- idx_ftp_logs_action          : action 값 8개 → 저카디널리티, 플래너가 seq scan 선호
-- idx_ftp_logs_username        : B-tree는 ILIKE '%...%' 미지원 → GIN(trgm)으로 대체
-- idx_ftp_logs_device_username : device_id+username 조합이나 ILIKE 미지원으로 무효
-- idx_devices_status           : status 값 3개 → 전체 row 대비 효과 없음

-- ────────────────────────────────────────────────────────────────────────────
-- 서비스 영향도 (부하로 인한 서비스 이상 감지)
-- ────────────────────────────────────────────────────────────────────────────

-- 장비 × 시간버킷(기본 10분) 집계. 롤업 잡이 ftp_logs에서 주기적으로 채운다.
CREATE TABLE IF NOT EXISTS service_metrics (
    device_id       INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    bucket          TIMESTAMPTZ NOT NULL,            -- 버킷 시작(UTC)
    transfers       INT    NOT NULL DEFAULT 0,       -- upload+download 건수
    transfer_fails  INT    NOT NULL DEFAULT 0,
    bytes           BIGINT NOT NULL DEFAULT 0,
    transfer_secs   DOUBLE PRECISION NOT NULL DEFAULT 0,  -- Σtransfer_time (throughput 계산용)
    login_attempts  INT    NOT NULL DEFAULT 0,       -- 식별된 계정 로그인 시도(성공+실패)
    login_fails     INT    NOT NULL DEFAULT 0,
    cwd_fails       INT    NOT NULL DEFAULT 0,       -- CWD 550 (디렉터리 없음) 건수
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (device_id, bucket)
);
ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS cwd_fails INT NOT NULL DEFAULT 0;
-- 큰 파일 전용 집계 (전송 속도 판정용). 작은 파일은 연결/인증 오버헤드가 전송시간의
-- 대부분이라 실효속도를 낮게 만들어, 소량 파일 대량 업로드가 성능 저하로 오인된다.
ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS xfers_big INT NOT NULL DEFAULT 0;
ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS bytes_big BIGINT NOT NULL DEFAULT 0;
ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS secs_big DOUBLE PRECISION NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_service_metrics_bucket ON service_metrics(bucket DESC);

-- baseline 이탈로 판정된 서비스 이상 이벤트.
CREATE TABLE IF NOT EXISTS service_alerts (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id    INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    bucket       TIMESTAMPTZ NOT NULL,
    metric       VARCHAR(30) NOT NULL,   -- fail_rate | throughput | login_fail_rate
    severity     VARCHAR(10) NOT NULL DEFAULT 'warning',  -- warning | critical
    value        DOUBLE PRECISION NOT NULL,
    baseline     DOUBLE PRECISION,
    threshold    DOUBLE PRECISION,
    sample_count INT,
    message      TEXT,
    notified     BOOLEAN NOT NULL DEFAULT FALSE,  -- 메일/웹훅 발송 여부
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (device_id, bucket, metric)   -- 동일 버킷·지표 중복 알림 방지
);
CREATE INDEX IF NOT EXISTS idx_service_alerts_created ON service_alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_service_alerts_unnotified ON service_alerts(notified) WHERE notified = FALSE;

-- ftp_logs action CHECK constraint 마이그레이션 (cwd_fail 추가)
-- pg_constraint 사용 (파티션 테이블 포함 신뢰성 높음)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ftp_logs_action_check'
          AND conrelid = 'ftp_logs'::regclass
    ) THEN
        ALTER TABLE ftp_logs DROP CONSTRAINT ftp_logs_action_check;
    END IF;
END $$;
-- NOT VALID: 기존 행 재검증 스킵 → 락 최소화 (기존 action 값은 모두 새 제약 충족)
ALTER TABLE ftp_logs ADD CONSTRAINT ftp_logs_action_check
    CHECK (action IN ('upload','download','delete','rename','login','logout','mkdir','rmdir','cwd_fail'))
    NOT VALID;

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS devices_updated_at ON devices;
CREATE TRIGGER devices_updated_at
    BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- ILIKE '%...%' 인덱스 지원

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

CREATE TABLE IF NOT EXISTS groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    group_type VARCHAR(50) NOT NULL CHECK (group_type IN ('telco', 'service', 'other')),
    description TEXT,
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
    action VARCHAR(20) NOT NULL CHECK (action IN ('upload', 'download', 'delete', 'rename', 'login', 'logout', 'mkdir', 'rmdir')),
    file_path TEXT,
    file_size BIGINT DEFAULT 0,
    transfer_time FLOAT DEFAULT 0,
    transfer_type VARCHAR(10),
    status VARCHAR(10) DEFAULT 'success' CHECK (status IN ('success', 'fail')),
    session_id VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, log_time)
) PARTITION BY RANGE (log_time);

-- 초기 파티션 생성 (당월 + 향후 2개월) — 이미 존재하면 건너뜀
DO $$
DECLARE
    base_date DATE := date_trunc('month', NOW())::DATE;
    i         INT;
    s         DATE;
    e         DATE;
    pname     TEXT;
BEGIN
    FOR i IN 0..2 LOOP
        s := (base_date + (i || ' months')::INTERVAL)::DATE;
        e := (s + '1 month'::INTERVAL)::DATE;
        pname := 'ftp_logs_' || to_char(s, 'YYYY_MM');
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = pname AND n.nspname = 'public'
        ) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF ftp_logs FOR VALUES FROM (%L) TO (%L)',
                pname, s, e
            );
            RAISE NOTICE 'Partition created: %', pname;
        END IF;
    END LOOP;
END $$;

-- DEFAULT 파티션: 매칭 파티션 없는 행 임시 보관 (신규 월 파티션 생성 전 안전망)
CREATE TABLE IF NOT EXISTS ftp_logs_default PARTITION OF ftp_logs DEFAULT;

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

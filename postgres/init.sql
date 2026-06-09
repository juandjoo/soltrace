CREATE EXTENSION IF NOT EXISTS "pgcrypto";

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
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

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
    PRIMARY KEY (device_id, group_id)
);

CREATE TABLE IF NOT EXISTS ftp_logs (
    id BIGSERIAL PRIMARY KEY,
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
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ftp_logs_device_time ON ftp_logs(device_id, log_time DESC);
CREATE INDEX IF NOT EXISTS idx_ftp_logs_username ON ftp_logs(username);
CREATE INDEX IF NOT EXISTS idx_ftp_logs_log_time ON ftp_logs(log_time DESC);
CREATE INDEX IF NOT EXISTS idx_ftp_logs_action ON ftp_logs(action);
CREATE INDEX IF NOT EXISTS idx_ftp_logs_device_username ON ftp_logs(device_id, username);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);

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

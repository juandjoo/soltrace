#!/usr/bin/env python3
"""
SolTrace FTP Daemon
proftpd 로그를 파싱하여 WAS(soltrace.mbone.net)에 전송한다.

TransferLog  : 업로드(i) / 다운로드(o) / 삭제(d) 이벤트
ExtendedAllLog: 로그인(230/PASS) / 로그아웃(QUIT) / 이름변경(RNTO) 이벤트
"""
import configparser
import hashlib
import json
import logging
import os
import platform
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import psutil
import requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.ini"

defaults = {
    "was_url": "http://soltrace.mbone.net",
    "transfer_log": "/usr/service/logs/proftpd/TransferLog",
    "extended_log": "/usr/service/logs/proftpd/ExtendedAllLog",
    "batch_size": "200",
    "poll_interval": "10",
    "heartbeat_interval": "60",
    "retry_max": "3",
    "retry_delay": "10",
    "http_timeout": "15",
    "ssl_verify": "true",     # false = self-signed 인증서 허용 (보안 주의)
    "update_url": "https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon",
    "max_buffer_lines": "50000",
    "state_dir": "/var/lib/soltrace",
    "buffer_file": "/var/lib/soltrace/buffer.jsonl",
    "log_file": "/var/log/soltrace-daemon.log",
    "log_level": "INFO",
}


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_dict({"daemon": defaults})
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE)
    return cfg


# ── Logging ───────────────────────────────────────────────────────────────────
def setup_logging(cfg: configparser.ConfigParser):
    level = getattr(logging, cfg["daemon"]["log_level"].upper(), logging.INFO)
    log_file = cfg["daemon"]["log_file"]
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file))
    except Exception:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


log = logging.getLogger("soltrace")


# ── Device Key ────────────────────────────────────────────────────────────────
def get_device_key() -> str:
    key_file = Path("/var/lib/soltrace/device.key")
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_text().strip()
    import secrets
    key = secrets.token_hex(16)
    key_file.write_text(key)
    return key


def get_proftpd_version() -> str:
    try:
        out = subprocess.check_output(["proftpd", "--version"], stderr=subprocess.STDOUT, timeout=5)
        return out.decode().strip().split("\n")[0][:50]
    except Exception:
        return "unknown"


def _get_os_pretty_name() -> str:
    """배포판 이름을 /etc/os-release에서 읽음. 실패하면 platform 폴백."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')[:100]
    except Exception:
        pass
    return f"{platform.system()} {platform.version()}"[:100]


# ── Log Parsers ───────────────────────────────────────────────────────────────

def parse_transfer_log(line: str) -> Optional[dict]:
    """
    xferlog format:
    DDD Mon DD HH:MM:SS YYYY transfer_time remote_host file_size filename
    transfer_type special_action direction access_mode username service auth_method auth_user completion
    """
    line = line.strip()
    if not line:
        return None
    parts = line.split()
    if len(parts) < 14:
        return None
    try:
        dt_str = f"{parts[1]} {parts[2]} {parts[3]} {parts[4]}"
        log_time = datetime.strptime(dt_str, "%b %d %H:%M:%S %Y").astimezone(timezone.utc)
        transfer_time = float(parts[5])
        client_ip = parts[6]
        file_size = int(parts[7])
        filename = parts[8].strip('"')
        transfer_type_code = parts[9]
        direction = parts[11]   # i=upload, o=download, d=delete
        username = parts[13]
        completion = parts[17] if len(parts) > 17 else "c"

        action = {"i": "upload", "o": "download", "d": "delete"}.get(direction)
        if not action:
            return None

        return {
            "log_time": log_time.isoformat(),
            "client_ip": client_ip,
            "username": username,
            "action": action,
            "file_path": filename,
            "file_size": file_size,
            "transfer_time": transfer_time,
            "transfer_type": "ascii" if transfer_type_code == "a" else "binary",
            "status": "success" if completion == "c" else "fail",
        }
    except (ValueError, IndexError):
        return None


_EXT_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+'
    r'(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+'
    r'"([^"]+)"\s+"([^"]*)"'
)
_rnfr_sessions: dict = {}


def parse_extended_log(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    m = _EXT_RE.match(line)
    if not m:
        return None

    dt_str, client_ip, username, pid, status_code, command, path, _, _, _, _ = m.groups()
    status_code = int(status_code)
    username = None if username == "-" else username
    path = path.strip('"')  # proftpd가 경로를 따옴표로 감싸는 경우 제거

    try:
        log_time = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
    except ValueError:
        return None

    entry = {
        "log_time": log_time.isoformat(),
        "client_ip": client_ip,
        "username": username,
        "status": "success",
        "file_size": 0,
        "transfer_time": 0.0,
        "session_id": pid,
    }

    if command == "PASS":
        if status_code == 230:
            entry["action"] = "login"
            return entry
        # 인증 실패(530 등) — username(id)이 식별된 경우만 서비스 영향으로 기록.
        # 빈/익명("-") 시도는 스캔성 노이즈이므로 제외 (위에서 username=None 처리됨).
        if username is not None:
            entry["action"] = "login"
            entry["status"] = "fail"
            return entry
        return None
    if command == "QUIT":
        entry["action"] = "logout"
        return entry
    if command == "RNFR":
        _rnfr_sessions[pid] = path
        return None
    if command == "RNTO" and status_code == 250:
        from_path = _rnfr_sessions.pop(pid, None)
        entry["action"] = "rename"
        entry["file_path"] = f"{from_path} -> {path}" if from_path else path
        return entry
    if command == "MKD" and status_code == 257:
        entry["action"] = "mkdir"
        entry["file_path"] = path
        return entry
    if command == "RMD" and status_code == 250:
        entry["action"] = "rmdir"
        entry["file_path"] = path
        return entry
    if command == "CWD" and status_code == 550:
        entry["action"] = "cwd_fail"
        entry["status"] = "fail"
        entry["file_path"] = path
        return entry

    return None


# ── File Tailer (with position persistence) ───────────────────────────────────

class FileTailer:
    """
    로그 파일을 폴링하고 위치(position)를 디스크에 저장한다.
    전송 성공 후 commit()을 호출해야 위치가 디스크에 반영된다.
    재시작 시 저장된 위치부터 읽어 중복 전송을 방지한다.
    """

    def __init__(self, path: str, parser, state_dir: str):
        self.path = path
        self.parser = parser
        # 파일별 고유 상태 파일 (로그 파일 경로 해시)
        slug = hashlib.md5(path.encode()).hexdigest()[:12]
        self.state_file = Path(state_dir) / f"{slug}.pos"
        Path(state_dir).mkdir(parents=True, exist_ok=True)

        self._pos, self._inode = self._load_state()
        self._pending_pos: Optional[int] = None  # 전송 확인 전 임시 위치

    def _load_state(self) -> Tuple[int, Optional[int]]:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                return int(data["pos"]), data.get("inode")
            except Exception:
                pass
        # 상태 없으면 파일 끝부터 읽기 시작 (기존 로그 재전송 방지)
        try:
            stat = os.stat(path=self.path)
            return stat.st_size, stat.st_ino
        except FileNotFoundError:
            return 0, None

    def poll(self) -> list:
        """새 로그 항목을 읽어 반환한다. commit() 전까지 위치는 디스크에 저장되지 않는다."""
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return []

        # 로테이션 감지 (inode 변경 또는 크기 감소)
        if stat.st_ino != self._inode or stat.st_size < self._pos:
            log.info("Log rotated: %s (resetting position)", self.path)
            self._pos = 0
            self._inode = stat.st_ino
            self._save_state()  # 로테이션은 즉시 커밋

        if stat.st_size <= self._pos:
            return []

        entries = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._pos)
            for line in f:
                entry = self.parser(line)
                if entry:
                    entries.append(entry)
            self._pending_pos = f.tell()

        return entries

    def commit(self):
        """전송 성공 후 위치를 디스크에 확정한다."""
        if self._pending_pos is not None:
            self._pos = self._pending_pos
            self._pending_pos = None
            self._save_state()

    def rollback(self):
        """전송 실패 시 pending 위치를 버린다 (다음 poll에서 재읽기 방지용 advance_on_fail로 대체)."""
        self._pending_pos = None

    def advance(self):
        """재전송 실패 후 버퍼 저장 완료 시 위치를 강제 진행시켜 재시작 후 중복을 막는다."""
        if self._pending_pos is not None:
            self._pos = self._pending_pos
            self._pending_pos = None
            self._save_state()

    def _save_state(self):
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pos": self._pos, "inode": self._inode}))
            tmp.replace(self.state_file)  # atomic rename
        except Exception as e:
            log.warning("Failed to save tailer state: %s", e)


# ── HTTP Client ───────────────────────────────────────────────────────────────

class WasClient:
    RETRY_MAX = 3
    RETRY_DELAY = 10  # 고정 10초 대기

    def __init__(self, base_url: str, device_key: str, http_timeout: int = 15, ssl_verify: bool = True):
        self.base_url = base_url.rstrip("/")
        self.device_key = device_key
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.session.timeout = http_timeout
        self.session.verify = ssl_verify

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        url = f"{self.base_url}/api/v1{path}"
        for attempt in range(1, self.RETRY_MAX + 1):
            try:
                r = self.session.post(url, json=payload)
                r.raise_for_status()
                return r.json() if r.text else {}
            except requests.exceptions.RequestException as e:
                log.warning("POST %s attempt %d/%d failed: %s", path, attempt, self.RETRY_MAX, e)
                if attempt < self.RETRY_MAX:
                    time.sleep(self.RETRY_DELAY)
        return None  # 3회 모두 실패

    def send_logs(self, entries: list) -> Optional[dict]:
        return self._post("/ingest/logs", {"device_key": self.device_key, "logs": entries})

    def register(self, hostname: str, ip: str, os_info: str, kernel_version: str, proftpd_ver: str) -> Optional[dict]:
        return self._post("/ingest/register", {
            "device_key": self.device_key,
            "hostname": hostname,
            "ip_address": ip,
            "os_info": os_info,
            "kernel_version": kernel_version,
            "proftpd_version": proftpd_ver,
            "daemon_version": "1.0.0",
        })

    def heartbeat(self, hostname: str, ip: str, status_payload: Optional[dict] = None) -> Optional[dict]:
        body = {"device_key": self.device_key, "hostname": hostname, "ip_address": ip}
        if status_payload:
            body.update(status_payload)
        return self._post("/ingest/heartbeat", body)


# ── Disk Buffer ───────────────────────────────────────────────────────────────

class DiskBuffer:
    """전송 실패 항목을 로컬에 보관한다. 원자적 쓰기로 부분 기록을 방지한다."""

    def __init__(self, path: str, max_lines: int = 50000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_lines = max_lines
        self._lock = threading.Lock()

    def write(self, entries: list):
        """버퍼에 항목을 추가한다 (atomic write). max_lines 초과 시 오래된 항목 제거."""
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            combined = self._read_raw() + entries
            if self.max_lines > 0 and len(combined) > self.max_lines:
                dropped = len(combined) - self.max_lines
                combined = combined[dropped:]
                log.warning("Buffer max_lines(%d) exceeded, dropped %d oldest entries", self.max_lines, dropped)
            with open(tmp, "w") as f:
                for e in combined:
                    f.write(json.dumps(e) + "\n")
            tmp.replace(self.path)

    def read_and_clear(self) -> list:
        """버퍼 전체를 읽고 삭제한다."""
        with self._lock:
            entries = self._read_raw()
            if entries:
                self.path.unlink(missing_ok=True)
            return entries

    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def _read_raw(self) -> list:
        if not self.path.exists():
            return []
        entries = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries


# ── Daemon ────────────────────────────────────────────────────────────────────

class SolTraceDaemon:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg["daemon"]
        self.running = False
        self._exit_code = 0
        self.hostname = socket.gethostname()
        self.ip = self._get_local_ip()
        self.device_key = get_device_key()
        self._start_time = time.monotonic()
        # 상태 추적
        self._daemon_status = "running"
        self._last_send_time: Optional[datetime] = None
        self._consecutive_failures = 0
        self._error_message: Optional[str] = None
        self._last_queue_size = 0
        # dirty tracking: 이전 전송값 기억 (임계값 이상 변화 시에만 전송)
        self._prev_status_sent: dict = {}

        state_dir = self.cfg["state_dir"]
        self.client = WasClient(
            base_url=self.cfg["was_url"],
            device_key=self.device_key,
            http_timeout=int(self.cfg["http_timeout"]),
            ssl_verify=self.cfg["ssl_verify"].lower() not in ("false", "0", "no"),
        )
        self.buffer = DiskBuffer(self.cfg["buffer_file"], max_lines=int(self.cfg["max_buffer_lines"]))
        self.batch_size = int(self.cfg["batch_size"])
        self.poll_interval = int(self.cfg["poll_interval"])
        self.heartbeat_interval = int(self.cfg["heartbeat_interval"])

        self.tailers = [
            FileTailer(self.cfg["transfer_log"], parse_transfer_log, state_dir),
            FileTailer(self.cfg["extended_log"], parse_extended_log, state_dir),
        ]

    def _collect_status(self, force: bool = False) -> dict:
        """
        상태 지표를 수집한다.
        force=False 이면 이전 전송값과 비교해 의미 있는 변화가 있는 필드만 반환.
        force=True 이면 (종료·오류 시) 전체 전송.
        """
        try:
            cpu = round(psutil.cpu_percent(interval=None), 1)
        except Exception:
            cpu = None
        try:
            mem_mb = round(psutil.virtual_memory().used / (1024 * 1024), 1)
        except Exception:
            mem_mb = None
        try:
            disk_free_gb = round(psutil.disk_usage(self.cfg["state_dir"]).free / (1024 ** 3), 2)
        except Exception:
            disk_free_gb = None

        current = {
            "daemon_status": self._daemon_status,
            "last_send_time": self._last_send_time.isoformat() if self._last_send_time else None,
            "buffer_lines": len(self.buffer._read_raw()) if self.buffer.exists() else 0,
            "queue_size": self._last_queue_size,
            "consecutive_failures": self._consecutive_failures,
            "error_message": self._error_message,
            "cpu_percent": cpu,
            "mem_mb": mem_mb,
            "disk_free_gb": disk_free_gb,
            "daemon_uptime": int(time.monotonic() - self._start_time),
        }

        if force:
            self._prev_status_sent = current.copy()
            return current

        # 임계값 이상 변화한 필드만 포함 (하트비트 payload 최소화)
        prev = self._prev_status_sent
        dirty = {}
        thresholds = {
            "cpu_percent": 5.0,      # ±5% 이상 변화 시
            "mem_mb": 20.0,          # ±20MB 이상 변화 시
            "disk_free_gb": 0.5,     # ±0.5GB 이상 변화 시
            "buffer_lines": 10,      # ±10건 이상 변화 시
            "queue_size": 5,
            "daemon_uptime": 60,     # 60초마다 갱신
            "consecutive_failures": 0,  # 0 = 변화 즉시 전송
        }
        for key, val in current.items():
            prev_val = prev.get(key)
            threshold = thresholds.get(key)
            if threshold is None:
                # 문자열·시간 필드: 값이 바뀌면 전송
                if val != prev_val:
                    dirty[key] = val
            elif val is None:
                if prev_val is not None:
                    dirty[key] = val
            elif prev_val is None or abs(val - prev_val) >= threshold:
                dirty[key] = val

        if dirty:
            self._prev_status_sent.update(dirty)
        return dirty  # 빈 dict 가능 (변화 없으면 heartbeat body 최소화)

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def _register(self):
        log.info("Registering device key=%s..., hostname=%s", self.device_key[:8], self.hostname)
        result = self.client.register(
            hostname=self.hostname, ip=self.ip,
            os_info=_get_os_pretty_name(),
            kernel_version=platform.release(),
            proftpd_ver=get_proftpd_version(),
        )
        if result:
            log.info("Device registered: status=%s id=%s", result.get("status"), result.get("device_id"))
        else:
            log.error("Registration failed (will retry next heartbeat)")

    def _self_update(self):
        """GitHub에서 최신 파일 다운로드 후 서비스 재시작."""
        update_url = self.cfg["daemon"].get("update_url", "").rstrip("/")
        if not update_url:
            log.warning("update_url not configured — skipping self-update")
            return

        files = ["requirements.txt", "soltrace_daemon.py", "soltrace_bulk.py"]
        tmps = []
        try:
            log.info("Self-update: downloading from %s", update_url)
            for fname in files:
                url = f"{update_url}/{fname}"
                tmp = BASE_DIR / f".{fname}.tmp"
                r = self.client.session.get(url, timeout=30)
                r.raise_for_status()
                tmp.write_bytes(r.content)
                tmps.append((tmp, BASE_DIR / fname))
                log.info("Downloaded: %s (%d bytes)", fname, len(r.content))

            # 모두 성공 → 교체
            for tmp, dst in tmps:
                tmp.replace(dst)
                log.info("Updated: %s", dst.name)

            # requirements 변경 대비 pip 업데이트
            venv_pip = BASE_DIR / "venv" / "bin" / "pip"
            if venv_pip.exists():
                subprocess.run(
                    [str(venv_pip), "install", "-r", str(BASE_DIR / "requirements.txt"), "-q"],
                    check=False,
                )

            log.info("Self-update complete — restarting service")
            subprocess.run(["systemctl", "restart", "soltrace-daemon"], check=False)

        except Exception as e:
            log.error("Self-update failed: %s", e)
            for tmp, _ in tmps:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    def _heartbeat_loop(self):
        # psutil CPU 측정은 첫 호출이 0을 반환하므로 워밍업
        psutil.cpu_percent(interval=None)
        while self.running:
            time.sleep(self.heartbeat_interval)
            if not self.running:
                break
            status_payload = self._collect_status()
            result = self.client.heartbeat(self.hostname, self.ip, status_payload)
            if result:
                log.debug("Heartbeat OK status=%s daemon=%s cpu=%.1f%% mem=%.0fMB",
                          result.get("status"),
                          status_payload.get("daemon_status"),
                          status_payload.get("cpu_percent") or 0,
                          status_payload.get("mem_mb") or 0)
                if result.get("update"):
                    log.info("Update requested by WAS — starting self-update")
                    threading.Thread(target=self._self_update, daemon=True).start()
            else:
                log.warning("Heartbeat failed")

    def _flush_startup_buffer(self) -> bool:
        """시작 시 이전 실패 버퍼를 먼저 전송한다. 실패 시 False 반환."""
        if not self.buffer.exists():
            return True
        buffered = self.buffer.read_and_clear()
        log.info("Sending %d buffered entries from previous run", len(buffered))
        result = self.client.send_logs(buffered)
        if result:
            log.info("Startup buffer flushed: accepted=%d", result.get("accepted", 0))
            return True
        # 버퍼 재전송 3회 실패 → 다시 저장 후 종료
        self.buffer.write(buffered)
        log.critical("Failed to flush startup buffer after %d retries - safe shutdown", WasClient.RETRY_MAX)
        return False

    def _safe_shutdown(self, unsent: list):
        """미전송 항목을 버퍼에 저장하고 위치를 확정한 뒤 종료한다."""
        if unsent:
            self.buffer.write(unsent)
            log.critical("Saved %d unsent entries to buffer: %s", len(unsent), self.cfg["buffer_file"])
        for tailer in self.tailers:
            tailer.advance()

        # 종료 전 상태를 WAS에 즉시 전송 (모니터링 목적, 전체 강제 전송)
        self._daemon_status = "stopping"
        try:
            self.client.heartbeat(self.hostname, self.ip, self._collect_status(force=True))
        except Exception:
            pass

        log.critical("Safe shutdown initiated - daemon will exit with code 1")
        self._exit_code = 1
        self.running = False

    def _sender_loop(self):
        while self.running:
            # 로그 파일 폴링
            all_entries = []
            for tailer in self.tailers:
                try:
                    entries = tailer.poll()
                    all_entries.extend(entries)
                except Exception as e:
                    log.warning("Tailer error: %s", e)

            self._last_queue_size = len(all_entries)
            if not all_entries:
                self._last_queue_size = 0
                time.sleep(self.poll_interval)
                continue

            # 전송 시도 (최대 3회, WasClient 내부에서 처리)
            result = self.client.send_logs(all_entries)

            if result:
                # 성공
                self._last_send_time = datetime.now(timezone.utc)
                self._consecutive_failures = 0
                self._error_message = None
                self._daemon_status = "running"
                for tailer in self.tailers:
                    tailer.commit()
                log.debug("Sent %d entries: accepted=%d rejected=%d",
                          len(all_entries), result.get("accepted", 0), result.get("rejected", 0))
            else:
                # 3회 모두 실패
                self._consecutive_failures += 1
                self._error_message = f"전송 {WasClient.RETRY_MAX}회 실패 ({len(all_entries)}건 미전송)"
                self._daemon_status = "error"
                log.critical("Send failed after %d retries (%d entries)",
                             WasClient.RETRY_MAX, len(all_entries))
                self._safe_shutdown(all_entries)
                return

    def start(self):
        self.running = True
        log.info("SolTrace daemon starting (WAS: %s)", self.cfg["was_url"])

        # 시작 시 이전 버퍼 먼저 전송
        if not self._flush_startup_buffer():
            sys.exit(1)

        self._register()

        hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="heartbeat")
        hb_thread.start()

        sender_thread = threading.Thread(target=self._sender_loop, daemon=True, name="sender")
        sender_thread.start()

        log.info("Daemon running (poll=%ss, batch=%d, retry=%d×%ds)",
                 self.poll_interval, self.batch_size, WasClient.RETRY_MAX, WasClient.RETRY_DELAY)

        def _stop(sig, _frame):
            log.info("Signal %s received, shutting down...", sig)
            self._daemon_status = "stopping"
            try:
                self.client.heartbeat(self.hostname, self.ip, self._collect_status(force=True))
            except Exception:
                pass
            self.running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while self.running:
            time.sleep(1)

        hb_thread.join(timeout=5)
        sender_thread.join(timeout=15)
        log.info("SolTrace daemon stopped (exit_code=%d)", self._exit_code)
        sys.exit(self._exit_code)


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()
    setup_logging(cfg)
    daemon = SolTraceDaemon(cfg)
    daemon.start()

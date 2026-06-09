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
from typing import Optional

import requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.ini"

defaults = {
    "was_url": "http://soltrace.mbone.net",
    "transfer_log": "/usr/service/logs/proftpd/TransferLog",
    "extended_log": "/usr/service/logs/proftpd/ExtendedAllLog",
    "batch_size": "100",
    "poll_interval": "5",
    "heartbeat_interval": "30",
    "retry_max": "5",
    "retry_delay": "10",
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
    hostname = socket.gethostname()
    raw = f"{hostname}-{socket.getfqdn()}"
    key = hashlib.sha256(raw.encode()).hexdigest()[:32]
    key_file.write_text(key)
    return key


def get_proftpd_version() -> str:
    try:
        out = subprocess.check_output(["proftpd", "--version"], stderr=subprocess.STDOUT, timeout=5)
        return out.decode().strip().split("\n")[0][:50]
    except Exception:
        return "unknown"


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
        # parts[0]=Day parts[1]=Mon parts[2]=dd parts[3]=HH:MM:SS parts[4]=YYYY
        dt_str = f"{parts[1]} {parts[2]} {parts[3]} {parts[4]}"
        log_time = datetime.strptime(dt_str, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
        transfer_time = float(parts[5])
        client_ip = parts[6]
        file_size = int(parts[7])
        filename = parts[8]
        transfer_type_code = parts[9]   # a=ascii, b=binary
        direction = parts[11]           # i=upload, o=download, d=delete
        username = parts[13]
        completion = parts[17] if len(parts) > 17 else "c"

        action_map = {"i": "upload", "o": "download", "d": "delete"}
        action = action_map.get(direction)
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


# ExtendedAllLog: YYYY-MM-DD HH:MM:SS ip user pid status_code command path - - "cmd_str" "response"
_EXT_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+'   # datetime
    r'(\S+)\s+'                                        # client_ip
    r'(\S+)\s+'                                        # username (- if none)
    r'(\d+)\s+'                                        # pid
    r'(\d+)\s+'                                        # status_code
    r'(\S+)\s+'                                        # command
    r'(\S+)\s+'                                        # path
    r'(\S+)\s+'                                        # bytes
    r'(\S+)\s+'                                        # transfer_time
    r'"([^"]+)"\s+'                                    # cmd_string
    r'"([^"]*)"'                                       # response
)

_rnfr_sessions: dict = {}   # pid -> rename_from path


def parse_extended_log(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    m = _EXT_RE.match(line)
    if not m:
        return None

    dt_str, client_ip, username, pid, status_code, command, path, _, _, cmd_str, response = m.groups()
    status_code = int(status_code)
    username = None if username == "-" else username

    try:
        log_time = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
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

    if command == "PASS" and status_code == 230:
        entry["action"] = "login"
        return entry

    if command == "QUIT" and status_code in (221, 221):
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

    return None


# ── File Tail ─────────────────────────────────────────────────────────────────

class FileTailer:
    """
    파일을 tail -f 방식으로 폴링. 로테이션(크기 감소) 감지 시 처음부터 다시 읽음.
    """
    def __init__(self, path: str, parser, result_queue: queue.Queue):
        self.path = path
        self.parser = parser
        self.queue = result_queue
        self._pos = 0
        self._inode = None
        self._seek_to_end()

    def _seek_to_end(self):
        try:
            stat = os.stat(self.path)
            self._inode = stat.st_ino
            self._pos = stat.st_size
        except FileNotFoundError:
            self._pos = 0
            self._inode = None

    def poll(self):
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return

        # File rotated or replaced
        if stat.st_ino != self._inode or stat.st_size < self._pos:
            log.info("Log rotated: %s", self.path)
            self._pos = 0
            self._inode = stat.st_ino

        if stat.st_size <= self._pos:
            return

        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._pos)
            for line in f:
                entry = self.parser(line)
                if entry:
                    self.queue.put(entry)
            self._pos = f.tell()


# ── HTTP Client ───────────────────────────────────────────────────────────────

class WasClient:
    def __init__(self, base_url: str, device_key: str, retry_max: int, retry_delay: int):
        self.base_url = base_url.rstrip("/")
        self.device_key = device_key
        self.retry_max = retry_max
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.session.timeout = 15

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        url = f"{self.base_url}/api/v1{path}"
        for attempt in range(1, self.retry_max + 1):
            try:
                r = self.session.post(url, json=payload)
                r.raise_for_status()
                return r.json() if r.text else {}
            except requests.exceptions.RequestException as e:
                log.warning("POST %s attempt %d/%d failed: %s", path, attempt, self.retry_max, e)
                if attempt < self.retry_max:
                    time.sleep(self.retry_delay * attempt)
        return None

    def register(self, hostname: str, ip: str, os_info: str, proftpd_ver: str) -> Optional[dict]:
        return self._post("/ingest/register", {
            "device_key": self.device_key,
            "hostname": hostname,
            "ip_address": ip,
            "os_info": os_info,
            "proftpd_version": proftpd_ver,
            "daemon_version": "1.0.0",
        })

    def heartbeat(self, hostname: str, ip: str) -> Optional[dict]:
        return self._post("/ingest/heartbeat", {
            "device_key": self.device_key,
            "hostname": hostname,
            "ip_address": ip,
        })

    def send_logs(self, entries: list) -> Optional[dict]:
        return self._post("/ingest/logs", {
            "device_key": self.device_key,
            "logs": entries,
        })


# ── Buffer (disk) ─────────────────────────────────────────────────────────────

class DiskBuffer:
    """전송 실패 시 로컬에 보관, 재시도 시 먼저 플러시."""
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, entries: list):
        with self._lock:
            with open(self.path, "a") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

    def read_and_clear(self) -> list:
        with self._lock:
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
            if entries:
                self.path.unlink(missing_ok=True)
            return entries


# ── Daemon ────────────────────────────────────────────────────────────────────

class SolTraceDaemon:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg["daemon"]
        self.running = False
        self._q: queue.Queue = queue.Queue(maxsize=10000)
        self.hostname = socket.gethostname()
        self.ip = self._get_local_ip()
        self.device_key = get_device_key()

        self.client = WasClient(
            base_url=self.cfg["was_url"],
            device_key=self.device_key,
            retry_max=int(self.cfg["retry_max"]),
            retry_delay=int(self.cfg["retry_delay"]),
        )
        self.buffer = DiskBuffer(self.cfg["buffer_file"])
        self.batch_size = int(self.cfg["batch_size"])
        self.poll_interval = int(self.cfg["poll_interval"])
        self.heartbeat_interval = int(self.cfg["heartbeat_interval"])

        self.tailers = []
        transfer_log = self.cfg["transfer_log"]
        extended_log = self.cfg["extended_log"]
        if Path(transfer_log).exists() or True:   # create tailer even if file doesn't exist yet
            self.tailers.append(FileTailer(transfer_log, parse_transfer_log, self._q))
        if extended_log and (Path(extended_log).exists() or True):
            self.tailers.append(FileTailer(extended_log, parse_extended_log, self._q))

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def _register(self):
        log.info("Registering device key=%s hostname=%s", self.device_key[:8] + "...", self.hostname)
        result = self.client.register(
            hostname=self.hostname,
            ip=self.ip,
            os_info=f"{platform.system()} {platform.release()}",
            proftpd_ver=get_proftpd_version(),
        )
        if result:
            log.info("Device status: %s (id=%s)", result.get("status"), result.get("device_id"))
        else:
            log.error("Registration failed, will retry next heartbeat")

    def _heartbeat_loop(self):
        while self.running:
            time.sleep(self.heartbeat_interval)
            if not self.running:
                break
            result = self.client.heartbeat(self.hostname, self.ip)
            if result:
                log.debug("Heartbeat OK, status=%s", result.get("status"))
            else:
                log.warning("Heartbeat failed")

    def _collect_batch(self) -> list:
        batch = []
        deadline = time.monotonic() + self.poll_interval
        while len(batch) < self.batch_size and time.monotonic() < deadline:
            try:
                entry = self._q.get(timeout=0.1)
                batch.append(entry)
            except queue.Empty:
                break
        return batch

    def _flush_buffer(self):
        buffered = self.buffer.read_and_clear()
        if buffered:
            log.info("Flushing %d buffered entries", len(buffered))
            result = self.client.send_logs(buffered)
            if not result:
                self.buffer.write(buffered)
                log.warning("Flush failed, re-buffered %d entries", len(buffered))
            else:
                log.info("Flushed: accepted=%d rejected=%d", result.get("accepted", 0), result.get("rejected", 0))

    def _sender_loop(self):
        while self.running:
            # Drain tailers
            for tailer in self.tailers:
                try:
                    tailer.poll()
                except Exception as e:
                    log.warning("Tailer error: %s", e)

            batch = self._collect_batch()

            # Try buffered first
            self._flush_buffer()

            if batch:
                result = self.client.send_logs(batch)
                if result:
                    log.debug("Sent %d entries: accepted=%d", len(batch), result.get("accepted", 0))
                else:
                    log.warning("Send failed, buffering %d entries", len(batch))
                    self.buffer.write(batch)

            if not batch:
                time.sleep(max(0, self.poll_interval - 0.5))

    def start(self):
        self.running = True
        log.info("SolTrace daemon starting (WAS: %s)", self.cfg["was_url"])
        self._register()

        hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb_thread.start()

        sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        sender_thread.start()

        log.info("Daemon running. Watching logs every %ss.", self.poll_interval)

        def _stop(sig, _frame):
            log.info("Signal %s received, shutting down...", sig)
            self.running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while self.running:
            time.sleep(1)

        hb_thread.join(timeout=5)
        sender_thread.join(timeout=10)
        log.info("SolTrace daemon stopped.")


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()
    setup_logging(cfg)
    daemon = SolTraceDaemon(cfg)
    daemon.start()

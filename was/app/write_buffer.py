"""
인제스트 로그를 메모리에 모아 주기적으로 bulk insert한다.
HTTP 요청이 DB 완료를 기다리지 않으므로 워커 블로킹을 제거한다.
"""
import logging
import threading
import time
from typing import List

log = logging.getLogger("soltrace.wbuf")


class WriteBuffer:
    def __init__(self, session_factory, flush_interval: int = 3, max_size: int = 2000):
        self._session_factory = session_factory
        self._flush_interval = flush_interval
        self._max_size = max_size
        self._queue: list = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="write-buffer"
        )
        self._thread.start()
        log.info("WriteBuffer started (flush=%ds max=%d)", self._flush_interval, self._max_size)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._flush()  # 종료 전 잔여 항목 최종 기록
        log.info("WriteBuffer stopped")

    def add(self, objects: list):
        flush_now = None
        with self._lock:
            self._queue.extend(objects)
            if len(self._queue) >= self._max_size:
                flush_now = self._queue[:]
                self._queue.clear()
        # 잠금 밖에서 flush (락 보유 시간 최소화)
        if flush_now:
            self._write(flush_now)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def _flush_loop(self):
        while self._running:
            time.sleep(self._flush_interval)
            self._flush()

    def _flush(self):
        with self._lock:
            if not self._queue:
                return
            batch = self._queue[:]
            self._queue.clear()
        self._write(batch)

    def _write(self, batch: list):
        if not batch:
            return
        try:
            db = self._session_factory()
            try:
                db.bulk_save_objects(batch)
                db.commit()
                log.debug("Flushed %d rows to DB", len(batch))
            finally:
                db.close()
        except Exception as e:
            log.error("WriteBuffer flush error (%d rows): %s", len(batch), e)


# 프로세스(워커)당 싱글턴
_instance: WriteBuffer = None


def init_buffer(session_factory, flush_interval: int = 3, max_size: int = 2000) -> WriteBuffer:
    global _instance
    _instance = WriteBuffer(session_factory, flush_interval, max_size)
    _instance.start()
    return _instance


def get_buffer() -> WriteBuffer:
    return _instance


def shutdown_buffer():
    if _instance:
        _instance.stop()

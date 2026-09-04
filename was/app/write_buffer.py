"""
인제스트 로그를 메모리에 모아 주기적으로 bulk insert한다.
HTTP 요청이 DB 완료를 기다리지 않으므로 워커 블로킹을 제거한다.

DB 쓰기 실패는 버리지 않는다
  /ingest/logs 는 버퍼에 넣자마자 accepted 를 돌려주므로, 데몬은 그 시점에 자기
  tailer 위치를 넘긴다. 여기서 배치를 버리면 그 로그는 어디에도 남지 않는다.
  그래서 실패한 배치는 재시도 큐에 되돌려 놓고 지수 백오프로 다시 쓴다
  (DB 재시작·스키마 DDL 락·커넥션 고갈처럼 대개 한때인 장애를 넘긴다).

  무한정 쌓이지는 않게 보관 행 수에 상한을 둔다. 상한에 닿으면 라우터가 503 을
  돌려주고, 데몬은 자기 디스크 버퍼에 담아 두었다가 나중에 다시 보낸다 —
  양쪽 모두 이미 있는 경로라 새로 만드는 장치가 없다.
"""
import logging
import threading
import time

from sqlalchemy.dialects.postgresql import insert as _pg_insert

log = logging.getLogger("soltrace.wbuf")

# GENERATED ALWAYS AS IDENTITY 및 서버 기본값 컬럼 제외
_SKIP_COLS = frozenset({"id", "created_at"})

# 재시도 정책. 백오프가 상한(60초)에 닿은 뒤로도 계속 시도하므로
# 시도 한도 100회는 대략 100분을 버틴다 (배포 중 스키마 락 장애를 넘기는 길이).
_RETRY_BASE_SEC = 2.0
_RETRY_MAX_SEC = 60.0
_MAX_ATTEMPTS = 100

# 재시도 보관 상한(행). 데몬의 max_buffer_lines 기본값과 같은 규모로 맞춘다.
# ORM 객체가 아니라 평문 dict 로 들고 있어 5만 행이 수십 MB 수준이다.
DEFAULT_MAX_PENDING = 50_000


class _Batch:
    """재시도를 기다리는 한 묶음. mappings 는 ORM 객체가 아니라 평문 dict 이다."""

    __slots__ = ("model", "mappings", "attempts", "next_at")

    def __init__(self, model, mappings: list):
        self.model = model
        self.mappings = mappings
        self.attempts = 0
        self.next_at = 0.0

    def __len__(self) -> int:
        return len(self.mappings)

    def defer(self) -> bool:
        """다음 재시도 시각을 뒤로 민다. 시도 한도를 넘겼으면 False."""
        self.attempts += 1
        if self.attempts >= _MAX_ATTEMPTS:
            return False
        self.next_at = time.monotonic() + min(
            _RETRY_BASE_SEC * 2 ** (self.attempts - 1), _RETRY_MAX_SEC
        )
        return True


def _to_mappings(objects: list):
    """ORM 객체 → (모델, dict 목록). 재시도 큐가 무거운 ORM 객체를 붙들지 않게 한 번만 변환한다."""
    model = type(objects[0])
    cols = [c.key for c in model.__mapper__.column_attrs if c.key not in _SKIP_COLS]
    return model, [{c: getattr(obj, c) for c in cols} for obj in objects]


class WriteBuffer:
    def __init__(self, session_factory, flush_interval: int = 3, max_size: int = 2000,
                 max_pending: int = DEFAULT_MAX_PENDING):
        self._session_factory = session_factory
        self._flush_interval = flush_interval
        self._max_size = max_size
        self._max_pending = max_pending
        self._queue: list = []
        self._retry: list[_Batch] = []
        self._retry_rows = 0
        self._dropped = 0
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="write-buffer"
        )
        self._thread.start()
        log.info("WriteBuffer started (flush=%ds max=%d pending_max=%d)",
                 self._flush_interval, self._max_size, self._max_pending)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._flush()                      # 종료 전 잔여 항목 최종 기록
        self._retry_due(force=True)        # 백오프를 무시하고 밀린 것도 한 번 더
        left = self.pending_rows()
        if left:
            log.error("WriteBuffer 종료 — 기록하지 못한 %d행이 남았습니다. "
                      "DB 상태를 확인하세요 (이 행들은 복구되지 않습니다).", left)
        log.info("WriteBuffer stopped (누적 유실 %d행)", self._dropped)

    # ── 입력 ─────────────────────────────────────────────────────────────────
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

    def pending_rows(self) -> int:
        """아직 DB 에 들어가지 못한 행 수 (대기 + 재시도)."""
        with self._lock:
            return len(self._queue) + self._retry_rows

    def dropped_rows(self) -> int:
        with self._lock:
            return self._dropped

    def saturated(self) -> bool:
        """더 받으면 버려야 하는 상태. 라우터가 이걸 보고 503 으로 되돌린다."""
        return self.pending_rows() >= self._max_pending

    # ── 쓰기 ─────────────────────────────────────────────────────────────────
    def _flush_loop(self):
        while self._running:
            time.sleep(self._flush_interval)
            self._flush()

    def _flush(self):
        self._retry_due()          # 밀린 것부터 비운다
        with self._lock:
            if not self._queue:
                return
            batch = self._queue[:]
            self._queue.clear()
        self._write(batch)

    def _write(self, objects: list):
        if not objects:
            return
        model, mappings = _to_mappings(objects)
        if self._insert(model, mappings):
            log.debug("Flushed %d rows to DB", len(mappings))
            return
        self._requeue(_Batch(model, mappings))

    def _insert(self, model, mappings: list) -> bool:
        """한 번의 bulk insert 시도. 성공 여부만 돌려준다."""
        try:
            db = self._session_factory()
            try:
                db.execute(_pg_insert(model).on_conflict_do_nothing(), mappings)
                db.commit()
                return True
            finally:
                db.close()
        except Exception as e:
            log.error("WriteBuffer 쓰기 실패 (%d행): %s", len(mappings), e)
            return False

    def _retry_due(self, force: bool = False):
        """재시도 시각이 된 배치를 다시 쓴다. force 면 백오프를 무시한다(종료 시)."""
        now = time.monotonic()
        due: list[_Batch] = []
        with self._lock:
            if not self._retry:
                return
            keep = []
            for b in self._retry:
                (due if (force or b.next_at <= now) else keep).append(b)
            self._retry = keep
            self._retry_rows -= sum(len(b) for b in due)
        for batch in due:
            if self._insert(batch.model, batch.mappings):
                log.info("WriteBuffer 재시도 성공 (%d행, %d회째)", len(batch), batch.attempts + 1)
            else:
                self._requeue(batch)

    def _requeue(self, batch: _Batch):
        """실패한 배치를 재시도 큐로. 시도 한도·보관 상한을 넘긴 것만 버린다."""
        if not batch.defer():
            self._drop([batch], f"재시도 {_MAX_ATTEMPTS}회 실패")
            return
        overflow: list[_Batch] = []
        with self._lock:
            self._retry.append(batch)
            self._retry_rows += len(batch)
            # 상한 초과분은 오래된 것부터 버린다. 라우터의 503 backpressure 가
            # 먼저 걸리므로 여기까지 오는 것은 이례적인 상황이다.
            while self._retry_rows > self._max_pending and len(self._retry) > 1:
                old = self._retry.pop(0)
                self._retry_rows -= len(old)
                overflow.append(old)
        if overflow:
            self._drop(overflow, f"재시도 보관 상한 {self._max_pending}행 초과")

    def _drop(self, batches: list, reason: str):
        rows = sum(len(b) for b in batches)
        with self._lock:
            self._dropped += rows
            total = self._dropped
        log.error("WriteBuffer 로그 유실 %d행 — %s (누적 %d행). "
                  "데몬은 이미 전송 완료로 처리했으므로 이 로그는 복구되지 않습니다.",
                  rows, reason, total)


# 프로세스(워커)당 싱글턴
_instance: WriteBuffer = None


def init_buffer(session_factory, flush_interval: int = 3, max_size: int = 2000,
                max_pending: int = DEFAULT_MAX_PENDING) -> WriteBuffer:
    global _instance
    _instance = WriteBuffer(session_factory, flush_interval, max_size, max_pending)
    _instance.start()
    return _instance


def get_buffer() -> WriteBuffer:
    return _instance


def shutdown_buffer():
    if _instance:
        _instance.stop()

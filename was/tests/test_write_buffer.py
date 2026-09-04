"""쓰기 버퍼 — DB 가 실패해도 로그를 버리지 않는지.

/ingest/logs 는 버퍼에 넣자마자 accepted 를 돌려주고 데몬은 그 시점에 tailer 위치를
넘긴다. 그래서 여기서 배치를 버리면 그 로그는 어디에도 남지 않는다.
"""
import pytest

from app import write_buffer as wb
from app.models import FtpLog


class _FakeDB:
    """execute/commit 만 흉내낸다. fail 을 켜 두면 쓰기가 실패한다."""

    def __init__(self, sink: list, state: dict):
        self.sink = sink
        self.state = state

    def execute(self, _stmt, mappings):
        if self.state["fail"]:
            raise RuntimeError("DB down")
        self.sink.extend(mappings)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def buf():
    """flush 스레드는 띄우지 않는다 — 테스트가 _flush()를 직접 불러 시점을 통제한다."""
    sink, state = [], {"fail": False}
    b = wb.WriteBuffer(lambda: _FakeDB(sink, state), flush_interval=1, max_size=10_000)
    b.sink, b.state = sink, state
    return b


def _rows(n, start=0):
    return [FtpLog(device_id=1, log_time=None, action="upload", row_hash=f"h{i}")
            for i in range(start, start + n)]


def test_db_failure_keeps_rows_for_retry(buf):
    buf.state["fail"] = True
    buf.add(_rows(5))
    buf._flush()

    assert buf.sink == []                 # 아직 못 씀
    assert buf.pending_rows() == 5        # 그러나 버려지지도 않음
    assert buf.dropped_rows() == 0


def test_retry_writes_rows_once_db_recovers(buf):
    buf.state["fail"] = True
    buf.add(_rows(5))
    buf._flush()
    assert buf.pending_rows() == 5

    buf.state["fail"] = False
    buf._retry_due(force=True)            # 백오프를 기다리지 않고 즉시

    assert len(buf.sink) == 5
    assert buf.pending_rows() == 0
    assert buf.dropped_rows() == 0


def test_backoff_defers_retry(buf):
    """실패 직후에는 다시 시도하지 않는다 (죽은 DB 를 매 틱 두드리지 않게)."""
    buf.state["fail"] = True
    buf.add(_rows(3))
    buf._flush()

    buf.state["fail"] = False
    buf._retry_due()                      # 아직 next_at 이 오지 않음
    assert buf.sink == []
    assert buf.pending_rows() == 3

    buf._retry_due(force=True)
    assert len(buf.sink) == 3


def test_attempts_are_capped(buf):
    """영구 오류(예: 잘못된 행)가 큐를 영원히 붙들지 않는다."""
    buf.state["fail"] = True
    buf.add(_rows(2))
    buf._flush()
    for _ in range(wb._MAX_ATTEMPTS + 1):
        buf._retry_due(force=True)

    assert buf.pending_rows() == 0
    assert buf.dropped_rows() == 2


def test_saturation_reports_backpressure():
    """보관 상한에 닿으면 saturated() 로 알린다 — 라우터가 503 을 돌려준다."""
    sink, state = [], {"fail": True}
    b = wb.WriteBuffer(lambda: _FakeDB(sink, state), flush_interval=1,
                       max_size=10_000, max_pending=10)
    assert not b.saturated()

    b.add(_rows(10))
    b._flush()

    assert b.saturated()
    assert b.pending_rows() == 10
    assert b.dropped_rows() == 0          # 상한에 '닿은' 것이지 넘긴 게 아니다


def test_overflow_drops_oldest_and_counts():
    """상한을 넘기면 오래된 배치부터 버린다 — 보관량은 갇히고, 잃은 행은 세어 둔다."""
    sink, state = [], {"fail": True}
    b = wb.WriteBuffer(lambda: _FakeDB(sink, state), flush_interval=1,
                       max_size=10_000, max_pending=10)
    for i in range(3):                    # 6행씩 세 배치 = 18행 > 상한 10
        b.add(_rows(6, start=i * 6))
        b._flush()

    # 넣은 18행이 어디로 갔는지가 다 설명돼야 한다 (조용히 사라진 행이 없어야 한다)
    assert b.pending_rows() + b.dropped_rows() == 18
    assert b.pending_rows() <= 10 + 6     # 상한 + 마지막 배치(항상 남긴다)
    assert b.dropped_rows() > 0

    # 남아 있는 것은 가장 최근 배치여야 한다 (오래된 쪽부터 버린다)
    state["fail"] = False
    b._retry_due(force=True)
    assert [m["row_hash"] for m in sink] == [f"h{i}" for i in range(12, 18)]


def test_ingest_returns_503_when_saturated(monkeypatch):
    """포화 상태에서는 accepted 를 주지 않는다.

    accepted 를 주면 데몬이 tailer 위치를 넘겨 버린다 — 받아 놓고 못 쓰면 그 로그는
    어디에도 남지 않는다. 503 을 주면 데몬이 자기 디스크 버퍼에 담아 두고 다시 보낸다.
    """
    from fastapi import HTTPException
    from app.routers import ingest
    from app.schemas import LogBatch, LogEntry

    class _DeviceDB:
        def query(self, _model):
            return self

        def filter(self, *_a):
            return self

        def first(self):
            return type("D", (), {"id": 1, "status": "confirmed"})()

    sink, state = [], {"fail": True}
    b = wb.WriteBuffer(lambda: _FakeDB(sink, state), flush_interval=1,
                       max_size=10_000, max_pending=10)
    b.add(_rows(10))
    b._flush()
    assert b.saturated()
    monkeypatch.setattr(wb, "_instance", b)

    payload = LogBatch(device_key="k", logs=[
        LogEntry(log_time="2026-09-04T00:00:00Z", action="upload"),
    ])
    with pytest.raises(HTTPException) as e:
        ingest.ingest_logs(payload, _DeviceDB())
    assert e.value.status_code == 503
    assert e.value.headers.get("Retry-After")


def test_stop_flushes_pending_retries(buf):
    """종료 시에는 백오프를 무시하고 남은 것을 마지막으로 한 번 더 쓴다."""
    buf.state["fail"] = True
    buf.add(_rows(4))
    buf._flush()
    assert buf.pending_rows() == 4

    buf.state["fail"] = False
    buf.stop()                            # 스레드는 없지만 stop 경로는 그대로 탄다

    assert len(buf.sink) == 4
    assert buf.pending_rows() == 0

"""데몬 생존 판정 — 하트비트·데몬 상태로 '지금 살아 있는가'를 정한다 (순수 함수).

장비 관리 화면·대시보드·이상 감지 알림이 모두 이 한 곳을 쓴다. 예전에는 120초 규칙이
장비 관리 화면(devices.js)에만 있어, 데몬이 '종료중'으로 멈춘 장비가 대시보드에서는
정상으로 세지고 알림도 나가지 않았다(2026-11-16).
"""
from datetime import datetime
from typing import Optional

# 하트비트 주기(기본 60초)의 두 배 — 한 번 늦은 것은 끊김으로 보지 않는다
HEARTBEAT_STALE_SEC = 120

# 화면 표시용 상태 (데몬이 보고한 값 위에 덧씌운다)
STOPPED = "stopped"   # 종료됨 — 마지막 보고가 '종료중'이고 그 뒤로 소식이 없다
LOST = "lost"         # 응답 없음 — 실행 중이라더니 하트비트가 끊겼다

# WAS 에서 일부러 끈 장비는 끊긴 게 아니다
_INTENTIONAL = {"disabled"}


def heartbeat_stale(last_heartbeat: Optional[datetime], now: datetime) -> bool:
    if last_heartbeat is None:
        return True
    return (now - last_heartbeat).total_seconds() > HEARTBEAT_STALE_SEC


def heartbeat_problem(daemon_status: Optional[str], last_heartbeat: Optional[datetime],
                      now: datetime) -> Optional[str]:
    """STOPPED | LOST | None. 하트비트가 살아 있으면 보고된 상태가 무엇이든 None.

    '종료중'인데 하트비트가 최근이면 막 내려간 참이다(자가 업데이트 재시작 등) —
    120초 안에 다시 올라오면 아무 일도 없었던 것으로 본다.
    """
    if daemon_status in _INTENTIONAL or not heartbeat_stale(last_heartbeat, now):
        return None
    return STOPPED if daemon_status == "stopping" else LOST


def effective_daemon_status(daemon_status: Optional[str], last_heartbeat: Optional[datetime],
                            now: datetime) -> str:
    """화면에 보일 데몬 상태 — 끊겼으면 STOPPED/LOST, 아니면 보고된 상태."""
    return heartbeat_problem(daemon_status, last_heartbeat, now) or (daemon_status or "unknown")


def minutes_since(last_heartbeat: Optional[datetime], now: datetime) -> Optional[float]:
    if last_heartbeat is None:
        return None
    return max((now - last_heartbeat).total_seconds() / 60.0, 0.0)

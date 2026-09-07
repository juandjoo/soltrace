"""default 파티션 재배치 실행 — 화면 버튼(설정 > DB 저장소)이 쓰는 백그라운드 작업.

SQL 은 여기에 다시 쓰지 않고 `scripts/rebalance_default_partition.sql` 을 그대로 실행한다.
서버에 들어가 psql 로 돌리든 화면 버튼으로 돌리든 **같은 파일 하나**가 돌아야, 한쪽만
고쳐져 갈라지는 일이 없다.

두 가지 이유로 백그라운드 스레드에서 돌린다:
  * 80만 행이면 20초, 더 쌓였으면 분 단위 — HTTP 응답 안에서 끝낼 수 있는 작업이 아니다.
  * 스크립트가 청크마다 COMMIT 하므로 트랜잭션 안에서 부를 수 없다(autocommit 필요).
"""
import logging
import os
import re
import threading
from datetime import datetime, timezone

from app.database import engine
from app.gitinfo import repo_dir

log = logging.getLogger("soltrace.rebalance")

SQL_PATH = ("scripts", "rebalance_default_partition.sql")

_lock = threading.Lock()
_state = {
    "running": False,
    "started_at": None,     # datetime | None
    "finished_at": None,    # datetime | None
    "ok": None,             # bool | None — 마지막 실행 결과
    "message": "",          # 사람이 읽을 한 줄
    "notices": [],          # DB 가 남긴 진행 메시지(RAISE NOTICE)
}


def _statements(sql: str) -> list[str]:
    """스크립트를 문장 단위로 쪼갠다.

    한 번에 여러 문장을 보내면 PostgreSQL 이 그것들을 하나의 트랜잭션으로 묶어버려,
    스크립트 안의 COMMIT 이 "잘못된 트랜잭션 마침"으로 거부된다. psql 이 그러듯 우리도
    문장을 하나씩 보내야 한다.

    문자열('...')·달러인용($do$...$do$)·줄주석(--) 안의 세미콜론은 구분자가 아니다.
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):                       # 줄 주석
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j]); i = j
        elif ch == "'":                                   # 문자열 ('' 는 escape)
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:min(j + 1, n)]); i = j + 1
        elif ch == "$" and (m := re.match(r"\$[A-Za-z_]*\$", sql[i:])):   # 달러 인용
            tag = m.group(0)
            j = sql.find(tag, i + len(tag))
            j = n if j < 0 else j + len(tag)
            buf.append(sql[i:j]); i = j
        elif ch == ";":
            out.append("".join(buf)); buf = []; i += 1
        else:
            buf.append(ch); i += 1
    out.append("".join(buf))
    # 주석과 공백뿐인 조각(파일 끝 설명 등)은 버린다
    return [t for t in (x.strip() for x in out)
            if re.sub(r"--[^\n]*", "", t).strip()]


def _drain_notices(raw) -> list[str]:
    """드라이버가 모아둔 진행 메시지를 가져오고 비운다.

    psycopg2 가 붙이는 "NOTICE:"/"알림:" 머리말은 화면에서 군더더기라 뗀다.
    """
    got = [re.sub(r"^(NOTICE|WARNING|알림|경고)\s*:\s*", "", n.strip())
           for n in getattr(raw, "notices", [])]
    if got:
        del raw.notices[:]
    return got


def status() -> dict:
    with _lock:
        return dict(_state, notices=list(_state["notices"]))


def _finish(ok: bool, message: str, notices: list[str]) -> None:
    with _lock:
        _state.update(
            running=False, ok=ok, message=message, notices=notices,
            finished_at=datetime.now(timezone.utc),
        )


def _run() -> None:
    path = os.path.join(repo_dir(), *SQL_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            sql = f.read()
    except OSError as e:
        log.error("재배치 스크립트를 읽을 수 없음: %s", e)
        _finish(False, f"재배치 스크립트를 읽을 수 없습니다: {path}", [])
        return

    notices: list[str] = []
    raw = None
    try:
        # AUTOCOMMIT: 스크립트가 청크마다 스스로 COMMIT 하므로 트랜잭션을 열어두면 안 된다
        # (풀에 돌려줄 때 SQLAlchemy 가 원래 격리수준으로 되돌린다).
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            raw = c.connection.driver_connection
            # 드라이버 커서로 직접 실행한다. SQLAlchemy 를 거치면 파라미터가 없어도 빈
            # 파라미터를 넘겨서 psycopg2 가 스크립트의 %I/%L/%s 를 먼저 먹고 죽는다
            # (그건 psycopg2 바인딩이 아니라 PostgreSQL format() 의 자리표시자다).
            with raw.cursor() as cur:
                for stmt in _statements(sql):
                    cur.execute(stmt)
                    if cur.description:                  # 마지막 확인용 SELECT
                        left = [" ".join(str(v) for v in row) for row in cur.fetchall()]
                        if left:
                            notices.append("붙지 않은 테이블: " + ", ".join(left)
                                           + " — 다시 실행하면 마저 붙입니다")
            notices = _drain_notices(raw) + notices
    except Exception as e:                                   # DB 오류 전반
        log.error("재배치 실패: %s", e)
        first = str(e).strip().splitlines()
        # 실패해도 어디까지 옮겼는지는 알려준다 — 청크마다 커밋했으므로 그만큼은 남아 있다.
        _finish(False, first[0] if first else "재배치에 실패했습니다.",
                _drain_notices(raw) + notices)
        return

    log.warning("default 파티션 재배치 완료 (관리자 요청)")
    _finish(True, "재배치를 마쳤습니다.", notices)


def start() -> bool:
    """이미 돌고 있으면 False. 시작했으면 True."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, ok=None, message="재배치를 진행하고 있습니다.",
                      notices=[], started_at=datetime.now(timezone.utc), finished_at=None)
    threading.Thread(target=_run, daemon=True, name="rebalance").start()
    return True

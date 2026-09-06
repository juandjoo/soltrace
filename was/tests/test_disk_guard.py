"""디스크 감시 경로 설정 — 잘못된 값이 사용률 판정으로 새지 않게.

경로가 상수였을 때는 PGDATA 를 다른 볼륨으로 옮기면 여유가 남은 `/` 를 계속 보게 돼
자동 정리가 영영 돌지 않았다. 설정값으로 뺀 뒤의 방어를 여기서 고정한다.
"""
import pytest

from app import disk_guard


class _FakeDB:
    """app_config 한 행만 흉내낸다 (get_config 는 SELECT value ... 만 쓴다)."""

    def __init__(self, value=None):
        self.value = value
        self.written = None

    def execute(self, *_args, **kwargs):
        db = self
        params = kwargs.get("parameters") or (_args[1] if len(_args) > 1 else None)
        if params and "v" in params:                 # set_config 의 INSERT
            db.written = params["v"]
            db.value = params["v"]

        class _Result:
            def first(self):
                return None if db.value is None else (db.value,)

        return _Result()

    def commit(self):
        pass


@pytest.mark.parametrize("stored,expected", [
    ("/data", "/data"),
    ("  /data/pgsql  ", "/data/pgsql"),      # 붙여넣기 공백
    (None, disk_guard.DEFAULT_PATH),
    ("", disk_guard.DEFAULT_PATH),
    ("data/pgsql", disk_guard.DEFAULT_PATH),  # 절대경로가 아니면 기본값
])
def test_get_path(stored, expected):
    assert disk_guard.get_path(_FakeDB(stored)) == expected


@pytest.mark.parametrize("path", [
    "data/pgsql",                 # 상대경로
    "/no/such/directory/for/soltrace/test",
])
def test_save_path_rejects(path):
    with pytest.raises(ValueError):
        disk_guard.save_path(_FakeDB(), path)


def test_save_path_accepts_existing_dir(tmp_path):
    db = _FakeDB()
    disk_guard.save_path(db, f"  {tmp_path} ")   # 공백·NBSP 는 떼고 저장
    assert db.written == str(tmp_path)


def test_save_path_empty_falls_back_to_root():
    db = _FakeDB()
    disk_guard.save_path(db, "   ")
    assert db.written == disk_guard.DEFAULT_PATH


def test_usage_unreadable_path_returns_zero():
    """읽을 수 없는 경로는 0 을 낸다 — enforce 는 이걸 보고 정리를 건너뛴다."""
    assert disk_guard.usage("/no/such/directory/for/soltrace/test") == (0, 0, 0.0)

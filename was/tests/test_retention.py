"""보존 기간 설정과 파티션 삭제 가드 — 잘못된 값이 파티션 생성/삭제로 새지 않게."""
import pytest

from app import retention
from app.routers.settings import _PARTITION_NAME


class _FakeDB:
    """app_config 한 행만 흉내낸다 (get_config 는 SELECT value ... 만 쓴다)."""

    def __init__(self, value):
        self.value = value

    def execute(self, *_args, **_kwargs):
        db = self

        class _Result:
            def first(self):
                return None if db.value is None else (db.value,)

        return _Result()


@pytest.mark.parametrize("stored,expected", [
    ("12", 12),
    ("  24  ", 24),            # 붙여넣기 공백
    (None, retention.DEFAULT_RETENTION_MONTHS),
    ("", retention.DEFAULT_RETENTION_MONTHS),
    ("abc", retention.DEFAULT_RETENTION_MONTHS),
    ("0", retention.DEFAULT_RETENTION_MONTHS),      # 범위 밖
    ("999", retention.DEFAULT_RETENTION_MONTHS),    # 범위 밖
])
def test_get_retention_months(stored, expected):
    assert retention.get_retention_months(_FakeDB(stored)) == expected


@pytest.mark.parametrize("months", [0, -1, 121])
def test_set_rejects_out_of_range(months):
    with pytest.raises(ValueError):
        retention.set_retention_months(_FakeDB(None), months)


@pytest.mark.parametrize("name", [
    "ftp_logs_2026_09", "ftp_logs_2025_01",
])
def test_partition_name_accepted(name):
    assert _PARTITION_NAME.match(name)


@pytest.mark.parametrize("name", [
    "ftp_logs_default",          # default 는 월별이 아니라 삭제 대상이 아니다
    "ftp_logs", "devices", "users",
    "ftp_logs_2026_09; DROP TABLE users",
    "ftp_logs_2026_9", "ftp_logs_26_09",
])
def test_partition_name_rejected(name):
    assert not _PARTITION_NAME.match(name)

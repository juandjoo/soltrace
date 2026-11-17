"""changelog.md 파싱 — 변경 이력 화면이 읽는 그대로."""
import re

from app.config import settings
from app.routers.settings import get_changelog


def test_parses_repo_changelog(monkeypatch):
    """저장소의 실제 changelog.md 를 날짜 → 항목으로 읽는다."""
    monkeypatch.setattr(settings, "repo_dir", "..", raising=False)
    entries = get_changelog(limit=5, _="admin")

    assert entries, "changelog.md 를 읽지 못했다"
    assert len(entries) <= 5
    for e in entries:
        assert len(e.date) == 10 and e.date[4] == "-"      # YYYY-MM-DD
    first = entries[0]
    assert first.items, "날짜 아래 항목이 비어 있다"
    # 항목 텍스트에는 커밋 해시 괄호가 남지 않는다 (해시는 commit 필드로 분리).
    # 제목 자체가 ')' 로 끝날 수는 있다 — 예: "…(2대씩, 데몬 1.3.0)" — 해시 모양만 본다.
    assert not re.search(r"\(`?[0-9a-f]{7,40}`?\)\s*$", first.items[0].text)
    assert first.items[0].commit is None or first.items[0].commit.isalnum()


def test_missing_file_returns_empty(monkeypatch, tmp_path):
    """changelog.md 가 없어도 화면이 죽지 않도록 빈 목록을 준다."""
    monkeypatch.setattr(settings, "repo_dir", str(tmp_path), raising=False)
    assert get_changelog(limit=5, _="admin") == []

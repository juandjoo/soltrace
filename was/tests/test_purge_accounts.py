"""업로드 자동 purge 계정 — 그룹 설정 입력 정규화."""
from app.purge_accounts import normalize_accounts, parse_accounts


def test_parse_accepts_lines_commas_spaces_and_dedupes_in_order():
    assert parse_accounts("cj_a\n cj_b, cj_a\tcj_c\r\n") == ["cj_a", "cj_b", "cj_c"]


def test_parse_empty():
    assert parse_accounts(None) == []
    assert parse_accounts("  \n ,") == []


def test_normalize_stores_one_per_line_or_none():
    assert normalize_accounts("b, a ,b") == "b\na"
    assert normalize_accounts(" \n") is None

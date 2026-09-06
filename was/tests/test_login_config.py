"""로그인 화면 설정 — 저장 전 검증과 이미지 형식 판정.

조회 엔드포인트가 인증 없이 열려 있으므로(로그인 전에 읽어야 한다) 여기 담기는 값이
'화면에 어차피 보이는 장식'을 벗어나지 않도록 키 목록도 함께 고정한다.
"""
import base64

import pytest

from app import login_config as lc


class _FakeDB:
    """app_config 를 dict 한 개로 흉내낸다 (get_config/set_config 만 쓴다)."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def execute(self, *args, **kwargs):
        params = kwargs.get("parameters") or (args[1] if len(args) > 1 else None) or {}
        store = self.values
        if "v" in params:                       # set_config 의 INSERT
            store[params["k"]] = params["v"]
            key = None
        else:
            key = params.get("k")

        class _Result:
            def first(self):
                if key is None or key not in store:
                    return None
                return (store[key],)

        return _Result()

    def commit(self):
        pass


# ── 이미지 형식 ──────────────────────────────────────────────────────────────

PNG  = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF  = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16


@pytest.mark.parametrize("data,mime", [
    (PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"),
])
def test_sniff_image(data, mime):
    assert lc.sniff_image(data) == mime


@pytest.mark.parametrize("data", [
    b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",   # SVG 는 받지 않는다
    b"<html>nope</html>",
    b"RIFF\x00\x00\x00\x00WAVE",                         # RIFF 지만 WebP 가 아님
    b"",
])
def test_sniff_image_rejects(data):
    with pytest.raises(ValueError):
        lc.sniff_image(data)


def test_to_data_uri_roundtrip():
    uri = lc.to_data_uri(PNG)
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == PNG


def test_validate_image_rejects_oversize():
    big = PNG + b"\x00" * lc.IMAGE_KINDS["logo"]["max_bytes"]
    with pytest.raises(ValueError, match="KB"):
        lc.validate_image("logo", big)
    # 같은 파일이라도 배경은 한도가 커서 통과한다
    assert lc.validate_image("bg", big).startswith("data:image/png;base64,")


def test_validate_image_unknown_kind():
    with pytest.raises(ValueError):
        lc.validate_image("favicon", PNG)


# ── 문구 · 색상 ──────────────────────────────────────────────────────────────

def test_normalize_text_strips_paste_artifacts():
    v = lc.normalize_text(" SolTrace ", " 로그인 ", " 경고\n둘째 줄 ", " #0D1B2A ")
    assert v["title"] == "SolTrace"
    assert v["subtitle"] == "로그인"
    assert v["warning"] == "경고\n둘째 줄"      # 줄바꿈은 그대로 남는다
    assert v["bg_color"] == "#0d1b2a"


@pytest.mark.parametrize("color", ["blue", "#fff", "#12345", "#12345g", "0d1b2a"])
def test_normalize_text_rejects_bad_color(color):
    with pytest.raises(ValueError):
        lc.normalize_text("", "", "", color)


@pytest.mark.parametrize("kwargs", [
    {"title": "가" * (lc.MAX_TITLE + 1)},
    {"subtitle": "나" * (lc.MAX_SUBTITLE + 1)},
    {"warning": "다" * (lc.MAX_WARNING + 1)},
])
def test_normalize_text_rejects_too_long(kwargs):
    args = {"title": "", "subtitle": "", "warning": "", "bg_color": ""} | kwargs
    with pytest.raises(ValueError):
        lc.normalize_text(**args)


# ── 저장 · 조회 ──────────────────────────────────────────────────────────────

def test_load_defaults_when_empty():
    c = lc.load(_FakeDB())
    assert c["title"] == lc.DEFAULT_TITLE
    assert c["subtitle"] == lc.DEFAULT_SUBTITLE
    assert c["warning"] == "" and c["bg_color"] == ""
    assert c["bg_image"] == "" and c["logo_image"] == ""


def test_save_and_load_roundtrip():
    db = _FakeDB()
    lc.save_text(db, "내부 로그", "사내 전용", "무단 접근 금지", "#123456")
    lc.save_image(db, "logo", PNG)
    c = lc.load(db)
    assert c["title"] == "내부 로그"
    assert c["subtitle"] == "사내 전용"
    assert c["warning"] == "무단 접근 금지"
    assert c["bg_color"] == "#123456"
    assert c["logo_image"].startswith("data:image/png;base64,")

    lc.clear_image(db, "logo")
    assert lc.load(db)["logo_image"] == ""


def test_load_returns_only_login_screen_keys():
    """인증 없이 내려가는 응답이라 계정·운영 정보가 섞이면 안 된다."""
    assert set(lc.load(_FakeDB())) == {
        "title", "subtitle", "warning", "bg_color", "bg_image", "logo_image",
    }

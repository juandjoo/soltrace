"""업로드 자동 purge — 경로 변환·토큰 추출·URL 조립."""
import pytest

from types import SimpleNamespace

from app.purge import PurgeApiError, build_purge_url, extract_token, pick_target, purge_path


@pytest.mark.parametrize("path,prefix,expected", [
    ("/mnt1/800/cjenm/CLIP/a.mp4", "/mnt1/800", "/cjenm/CLIP/a.mp4"),
    ("/mnt1/800/cjenm/CLIP/a.mp4", "/mnt1/800/cjenm/", "/CLIP/a.mp4"),
    ("/mnt1/800/cjenm/CLIP/a.mp4", None, "/mnt1/800/cjenm/CLIP/a.mp4"),
    ("/mnt1/8000/a.mp4", "/mnt1/800", None),          # 폴더 중간에서 끊기면 불일치
    ("/other/a.mp4", "/mnt1/800", None),
])
def test_purge_path(path, prefix, expected):
    assert purge_path(path, prefix) == expected


@pytest.mark.parametrize("headers,body,expected", [
    ({"X-Subject-Token": "hdr"}, "{}", "hdr"),
    ({"Authorization": "Bearer abc"}, "", "abc"),
    ({}, '{"token": "t1"}', "t1"),
    ({}, '{"access_token": "t2", "expires_in": 3600}', "t2"),
    ({}, '{"token": {"id": "t3", "expires": "x"}}', "t3"),
    ({}, '{"data": {"token": "t4"}}', "t4"),
    ({}, '"plain-token"', "plain-token"),
    ({}, '{"id": 7, "name": "x"}', None),              # 최상위 id 는 토큰이 아니다
    ({}, "<html>error page</html>", None),
])
def test_extract_token(headers, body, expected):
    assert extract_token(headers, body)[0] == expected


def test_build_purge_url_quotes_parts():
    url = build_purge_url("http://h:8000/v2/management/service/{service}/volume/{volume}/purge", "my svc", "v/1")
    assert url == "http://h:8000/v2/management/service/my%20svc/volume/v%2F1/purge"


def test_build_purge_url_requires_placeholders():
    with pytest.raises(PurgeApiError):
        build_purge_url("http://h/purge", "s", "v")


# 같은 FTP 계정을 마운트 경로별로 — 경로가 맞는 것 중 가장 긴 마운트 경로가 이긴다
_CLIP = SimpleNamespace(id=1, strip_prefix="/mnt1/800/cjenm")
_ROOT = SimpleNamespace(id=2, strip_prefix="/mnt1/800")
_ANY = SimpleNamespace(id=3, strip_prefix=None)


@pytest.mark.parametrize("targets,path,expected_id,expected_path", [
    ([_CLIP, _ROOT], "/mnt1/800/cjenm/CLIP/a.mp4", 1, "/CLIP/a.mp4"),
    ([_ROOT, _CLIP], "/mnt1/800/cjenm/CLIP/a.mp4", 1, "/CLIP/a.mp4"),   # 순서와 무관
    ([_CLIP, _ROOT], "/mnt1/800/tving/b.mp4", 2, "/tving/b.mp4"),
    ([_CLIP, _ANY], "/other/c.mp4", 3, "/other/c.mp4"),                 # 빈 마운트 경로는 기본값
    ([_CLIP, _ROOT], "/other/c.mp4", None, None),                       # 아무 데도 안 맞음
])
def test_pick_target_longest_mount(targets, path, expected_id, expected_path):
    t, p = pick_target(targets, path)
    assert (t.id if t else None, p) == (expected_id, expected_path)

"""업로드 자동 purge — 경로 변환·토큰 추출·URL 조립."""
import pytest

from app.purge import PurgeApiError, build_purge_url, extract_token, purge_path


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

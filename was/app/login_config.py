"""로그인 화면 설정 — 제목·안내문·경고문·배경·로고.

값은 app_config 에 문자열로 담는다(이미지는 data URI). retention.py / disk_guard.py 와
같은 방식으로 **기본값·허용 범위·저장 키를 이 모듈 한 곳에만** 둔다 — 라우터와 화면이
같은 규칙을 보게 하기 위해서다.

조회는 로그인 **전에** 필요하므로 인증이 없다(routers/settings.py 의 GET /login-config).
그래서 여기에는 로그인 화면에 어차피 그려지는 장식 값만 담는다 — 계정·IP·임계값 같은
운영 정보는 절대 넣지 않는다.

이미지는 파일로 두지 않고 data URI 로 app_config 에 넣는다. 배포(update_rocky8.sh)가
저장소를 통째로 갱신해도 업로드한 이미지가 날아가지 않고, 백업이 DB 하나로 끝난다.
CSP 는 `img-src 'self' data:` 라 data URI 가 그대로 그려진다(main.py).
"""
import base64
import logging
import re

from sqlalchemy.orm import Session

from app.security import get_config, set_config, strip_input

log = logging.getLogger("soltrace.login_config")

TITLE_KEY    = "login_title"
SUBTITLE_KEY = "login_subtitle"
WARNING_KEY  = "login_warning"
BG_COLOR_KEY = "login_bg_color"

DEFAULT_TITLE    = "SolTrace"
DEFAULT_SUBTITLE = "FTP Log Analyzer에 로그인하세요."

MAX_TITLE    = 40
MAX_SUBTITLE = 120
MAX_WARNING  = 2000

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# 업로드 이미지 종류. 키·크기 한도·안내 문구만 다르고 처리는 같다 —
# 라우터가 종류별로 갈라지지 않게 여기서 표로 둔다.
IMAGE_KINDS = {
    "bg":   {"key": "login_bg_image",   "max_bytes": 2 * 1024 * 1024, "label": "배경 이미지"},
    "logo": {"key": "login_logo_image", "max_bytes": 512 * 1024,      "label": "로고 이미지"},
}

# 매직 바이트로 형식을 판정한다. 브라우저가 보낸 Content-Type 은 위조할 수 있고,
# 그 값이 그대로 data URI 의 MIME 이 되므로 파일 내용에서 직접 읽는다.
_MAGIC = (
    (b"\xff\xd8\xff",          "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n",     "image/png"),
    (b"GIF87a",                "image/gif"),
    (b"GIF89a",                "image/gif"),
)


def sniff_image(data: bytes) -> str:
    """이미지 MIME 타입 판정. 알 수 없는 형식이면 ValueError."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    # WebP: 'RIFF' + 4바이트 길이 + 'WEBP'
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("jpg · png · gif · webp 이미지만 올릴 수 있습니다")


def to_data_uri(data: bytes) -> str:
    return f"data:{sniff_image(data)};base64,{base64.b64encode(data).decode('ascii')}"


def _size_label(nbytes: int) -> str:
    return f"{nbytes // 1024 // 1024}MB" if nbytes >= 1024 * 1024 else f"{nbytes // 1024}KB"


def validate_image(kind: str, data: bytes) -> str:
    """업로드 바이트 → data URI. 종류·크기·형식을 모두 확인한다."""
    spec = IMAGE_KINDS.get(kind)
    if spec is None:
        raise ValueError(f"알 수 없는 이미지 종류입니다: {kind}")
    if not data:
        raise ValueError("빈 파일입니다")
    if len(data) > spec["max_bytes"]:
        raise ValueError(f"{spec['label']}는 {_size_label(spec['max_bytes'])} 이하만 올릴 수 있습니다")
    return to_data_uri(data)


def normalize_text(title: str, subtitle: str, warning: str, bg_color: str) -> dict:
    """입력 문자열 정리·검증. 잘못된 값은 ValueError(사용자에게 그대로 보여줄 문구)."""
    title    = strip_input(title or "")
    subtitle = strip_input(subtitle or "")
    warning  = strip_input(warning or "")
    bg_color = strip_input(bg_color or "")

    if len(title) > MAX_TITLE:
        raise ValueError(f"제목은 {MAX_TITLE}자 이하로 입력하세요")
    if len(subtitle) > MAX_SUBTITLE:
        raise ValueError(f"안내 문구는 {MAX_SUBTITLE}자 이하로 입력하세요")
    if len(warning) > MAX_WARNING:
        raise ValueError(f"경고 메시지는 {MAX_WARNING}자 이하로 입력하세요")
    if bg_color and not _HEX_COLOR.match(bg_color):
        raise ValueError("배경 색상은 #RRGGBB 형식으로 입력하세요 (예: #0d1b2a)")

    return {"title": title, "subtitle": subtitle, "warning": warning,
            "bg_color": bg_color.lower()}


def load(db: Session) -> dict:
    """로그인 화면에 그릴 값 전부. 비어 있으면 기본값으로 채워 화면이 늘 뭔가를 보여주게 한다."""
    return {
        "title":      get_config(db, TITLE_KEY) or DEFAULT_TITLE,
        "subtitle":   get_config(db, SUBTITLE_KEY) or DEFAULT_SUBTITLE,
        "warning":    get_config(db, WARNING_KEY) or "",
        "bg_color":   get_config(db, BG_COLOR_KEY) or "",
        "bg_image":   get_config(db, IMAGE_KINDS["bg"]["key"]) or "",
        "logo_image": get_config(db, IMAGE_KINDS["logo"]["key"]) or "",
    }


def save_text(db: Session, title: str, subtitle: str, warning: str, bg_color: str) -> None:
    v = normalize_text(title, subtitle, warning, bg_color)
    set_config(db, TITLE_KEY, v["title"])
    set_config(db, SUBTITLE_KEY, v["subtitle"])
    set_config(db, WARNING_KEY, v["warning"])
    set_config(db, BG_COLOR_KEY, v["bg_color"])


def save_image(db: Session, kind: str, data: bytes) -> None:
    set_config(db, IMAGE_KINDS[kind]["key"], validate_image(kind, data))
    log.info("로그인 화면 %s 저장 (%s bytes)", IMAGE_KINDS[kind]["label"], len(data))


def clear_image(db: Session, kind: str) -> None:
    if kind not in IMAGE_KINDS:
        raise ValueError(f"알 수 없는 이미지 종류입니다: {kind}")
    set_config(db, IMAGE_KINDS[kind]["key"], "")
    log.info("로그인 화면 %s 제거", IMAGE_KINDS[kind]["label"])

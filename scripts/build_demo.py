#!/usr/bin/env python3
"""샘플(데모) 페이지 생성 — was/static 의 실제 화면을 단일 HTML 파일로 묶는다.

실제 index.html / css / js 를 그대로 인라인하고, API 응답만 demo-data.js 의
가짜 데이터로 대체한다. 화면을 고치면 이 스크립트를 다시 돌리기만 하면 되므로
샘플이 실물과 따로 낡지 않는다.

실행:
    python3 scripts/build_demo.py            # samples/soltrace-demo.html 생성
    python3 scripts/build_demo.py -o out.html

결과 파일은 브라우저로 바로 열 수 있다(서버 불필요). 다만 Bootstrap/Chart.js 는
CDN 에서 받으므로 인터넷 연결이 필요하다.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "was" / "static"
DEFAULT_OUT = ROOT / "samples" / "soltrace-demo.html"

# index.html 의 <script src="/static/js/X.js?v=__VER__"> 순서를 그대로 따른다.
# demo-data.js 는 utils.js 보다 먼저 실행되어야 한다(토큰 주입 + fetch 가로채기).
DEMO_FIRST = "demo-data.js"


def _read(path: Path) -> str:
    if not path.is_file():
        sys.exit(f"파일을 찾을 수 없습니다: {path}")
    return path.read_text(encoding="utf-8")


def build() -> str:
    html = _read(STATIC / "index.html")

    # 1) 로컬 CSS 인라인 (CDN 링크는 그대로 둔다)
    css = _read(STATIC / "css" / "app.css")
    html, n = re.subn(
        r'<link href="/static/css/app\.css[^"]*" rel="stylesheet">',
        f"<style>\n{css}\n</style>",
        html,
    )
    if n != 1:
        sys.exit(f"app.css link 치환 실패 (matched={n}) — index.html 구조를 확인하세요")

    # 2) favicon 은 단일 파일에서 참조할 수 없으므로 제거
    html = re.sub(r'\s*<link rel="icon"[^>]*>', "", html)

    # 3) 로컬 JS 인라인 — src 순서 유지, demo-data.js 를 맨 앞에 끼워 넣는다
    names = re.findall(r'<script src="/static/js/([^"?]+)[^"]*"></script>', html)
    if not names:
        sys.exit("index.html 에서 /static/js 스크립트를 찾지 못했습니다")

    bundle = [f"/* === {DEMO_FIRST} === */\n" + _read(STATIC / "js" / DEMO_FIRST)]
    bundle += [f"/* === {n} === */\n" + _read(STATIC / "js" / n) for n in names]
    inline = "<script>\n" + "\n".join(bundle) + "\n</script>"

    # 첫 스크립트 태그를 번들로 바꾸고 나머지는 제거
    html = re.sub(r'<script src="/static/js/[^"]*"></script>', "\x00", html)
    first, rest = html.split("\x00", 1)
    html = first + inline + rest.replace("\x00", "")

    # 4) 버전 자리표시자 + 제목
    html = html.replace("__VER__", "demo")
    html = html.replace(
        "<title>SolTrace - FTP Log Analyzer</title>",
        "<title>SolTrace 데모 — FTP Log Analyzer</title>",
    )
    header = (
        "<!--\n"
        "  이 파일은 scripts/build_demo.py 로 생성된 샘플 페이지입니다. 직접 수정하지 마세요.\n"
        "  화면을 바꾸려면 was/static 을 고친 뒤 스크립트를 다시 실행하세요.\n"
        "-->\n"
    )
    return header + html


def main():
    ap = argparse.ArgumentParser(description="SolTrace 샘플(데모) 페이지 생성")
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT, help="출력 파일 경로")
    args = ap.parse_args()

    out = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out, encoding="utf-8")
    print(f"생성 완료: {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}"
          f" ({len(out):,} bytes)")
    print("브라우저로 바로 열어 확인하세요 (Bootstrap/Chart.js 는 CDN 사용 → 인터넷 필요)")


if __name__ == "__main__":
    main()

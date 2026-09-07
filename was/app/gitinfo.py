"""git 조회 헬퍼 — 설정 페이지 버전 표시와 정적 파일 캐시 버스팅(main)이 공유한다.

저장소 위치는 settings.repo_dir(운영: /opt/soltrace). 그 경로에 .git 이 없으면(로컬 개발)
현재 디렉터리를 쓴다. 데몬 최신 버전도 같은 저장소에서 읽는다 — WAS 설정에 따로 적어 두면
데몬을 올릴 때마다 두 곳을 맞춰야 하고, 한쪽만 고쳐지면 조용히 어긋난다.
"""
import os
import re
import subprocess

from app.config import settings


def repo_dir() -> str:
    """배포된 저장소 경로. 버전 조회와 changelog.md 읽기가 같은 경로를 본다."""
    return settings.repo_dir if os.path.isdir(os.path.join(settings.repo_dir, ".git")) else "."


def git_run(*args: str, timeout: int = 10):
    """git 실행 결과(CompletedProcess). 실행 자체가 불가능하면 None."""
    repo = repo_dir()
    # safe.directory: WAS(soltrace)가 root 소유로 바뀐 .git 에서도 git 실행 가능하게
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={repo}", "-C", repo, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git(*args: str, timeout: int = 10) -> str | None:
    """성공 시 stdout(strip), 실패 시 None."""
    r = git_run(*args, timeout=timeout)
    return r.stdout.strip() if (r and r.returncode == 0) else None


# ── 데몬 버전 ────────────────────────────────────────────────────────────────
# 유일한 출처는 ftp-daemon/soltrace_daemon.py 의 DAEMON_VERSION.
# 데몬이 자가 업데이트로 받아 가는 파일과 WAS 가 "최신"으로 삼는 값이 같은 파일이라
# 배포만 하면 기준이 저절로 맞는다.
_DAEMON_VER_RE = re.compile(r"""^DAEMON_VERSION\s*=\s*['"]([^'"]+)['"]""", re.M)
_daemon_ver_cache: tuple[float, str | None] = (0.0, None)


def _version_tuple(v: str | None) -> tuple[int, ...] | None:
    """'1.1.2' -> (1, 1, 2). 숫자를 못 찾으면 None (판정 불가)."""
    nums = re.findall(r"\d+", v or "")
    if not nums:
        return None
    parts = [int(x) for x in nums[:3]]
    return tuple(parts + [0] * (3 - len(parts)))     # 1.1 과 1.1.0 을 같게 본다


def daemon_outdated(reported: str | None) -> bool:
    """장비가 보고한 버전이 배포본보다 **낮은가**.

    '다르면 구버전' 으로 두면, WAS 배포가 데몬보다 뒤처졌을 때(장비가 먼저 자가 업데이트로
    올라간 경우) 더 새 버전이 구버전으로 표시된다. 실제로 그랬다 — 데몬 1.1.2 가 저장소
    1.1.1 을 기준으로 ⚠︎ 를 달았다. 비교는 낮은 쪽만 본다.

    한쪽이라도 모르면(미보고, 파일 없음, 형식 불명) 판정하지 않는다 — 모르는 것을 구버전이라
    표시하면 진짜 구버전이 묻힌다.
    """
    latest = latest_daemon_version()
    if not latest or not reported or reported == latest:
        return False
    a, b = _version_tuple(reported), _version_tuple(latest)
    if a is None or b is None:
        return False
    return a < b


def latest_daemon_version() -> str | None:
    """배포된 저장소가 담고 있는 데몬 버전. 파일이 없거나 못 읽으면 None."""
    global _daemon_ver_cache
    path = os.path.join(repo_dir(), "ftp-daemon", "soltrace_daemon.py")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached_mtime, cached_ver = _daemon_ver_cache
    if cached_mtime == mtime:
        return cached_ver
    try:
        # 상수는 파일 앞부분에 있다 — 전체를 읽지 않는다.
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return None
    m = _DAEMON_VER_RE.search(head)
    ver = m.group(1) if m else None
    _daemon_ver_cache = (mtime, ver)
    return ver

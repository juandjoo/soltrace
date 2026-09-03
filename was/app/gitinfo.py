"""git 조회 헬퍼 — 설정 페이지 버전 표시와 정적 파일 캐시 버스팅(main)이 공유한다.

저장소 위치는 settings.repo_dir(운영: /opt/soltrace). 그 경로에 .git 이 없으면(로컬 개발)
현재 디렉터리를 쓴다.
"""
import os
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

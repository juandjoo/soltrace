"""테스트 부트스트랩: 앱 설정 검증(SECRET_KEY/ADMIN_PASSWORD 기본값 거부)을 통과시키는 env 주입.

실행: cd was && python -m pytest tests -q
DB 연결은 하지 않는다 (engine 생성만 되고 접속은 지연).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("DATABASE_URL", "postgresql://soltrace:x@127.0.0.1:1/soltrace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

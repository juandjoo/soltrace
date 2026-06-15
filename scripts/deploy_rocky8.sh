#!/usr/bin/env bash
# SolTrace 배포 - GitLab + GitHub push 후 Rocky Linux 8 WAS 원격 업데이트
# 사용법: bash scripts/deploy_rocky8.sh [브랜치명]  (기본: main)
#
# 환경변수로 WAS 접속 정보를 지정한다 (또는 ~/.ssh/config 활용).
#   WAS_HOST  : WAS 서버 IP 또는 호스트명        (필수)
#   WAS_USER  : SSH 접속 유저                    (기본: rocky)
#   WAS_KEY   : SSH 개인키 경로                  (기본: ~/.ssh/id_rsa)
#   WAS_REPO  : 서버 내 soltrace 저장소 경로     (기본: ~/soltrace)
#
# 예) WAS_HOST=192.168.0.10 bash scripts/deploy_rocky8.sh

set -euo pipefail

BRANCH="${1:-main}"
WAS_HOST="${WAS_HOST:-}"
WAS_USER="${WAS_USER:-rocky}"
WAS_KEY="${WAS_KEY:-$HOME/.ssh/id_rsa}"
WAS_REPO="${WAS_REPO:-~/soltrace}"
RESULT=0

# ── git push ────────────────────────────────────────────────────────────────
echo "[1/3] GitLab push (origin/$BRANCH)"
git push origin "$BRANCH" || { echo "WARN: GitLab push 실패"; RESULT=1; }

echo "[2/3] GitHub push (github/$BRANCH)"
git push github "$BRANCH" || { echo "WARN: GitHub push 실패"; RESULT=1; }

# ── 원격 WAS 업데이트 ────────────────────────────────────────────────────────
echo "[3/3] WAS 원격 업데이트"

if [ -z "$WAS_HOST" ]; then
    echo "WARN: WAS_HOST 미설정 — 원격 업데이트 생략"
    echo "      수동 실행: ssh $WAS_USER@<WAS_IP> 'cd $WAS_REPO && sudo bash scripts/update_rocky8.sh $BRANCH'"
    RESULT=1
else
    SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
    [ -f "$WAS_KEY" ] && SSH_OPTS+=(-i "$WAS_KEY")

    ssh "${SSH_OPTS[@]}" "$WAS_USER@$WAS_HOST" \
        "cd $WAS_REPO && sudo bash scripts/update_rocky8.sh $BRANCH" \
    || { echo "ERROR: WAS 원격 업데이트 실패"; RESULT=1; }
fi

# ── 결과 ─────────────────────────────────────────────────────────────────────
echo ""
if [ "$RESULT" -eq 0 ]; then
    echo "Done: $BRANCH → GitLab + GitHub + WAS($WAS_HOST) 업데이트 완료"
else
    echo "Done: $BRANCH (일부 실패 — 위 경고 확인)"
fi
exit "$RESULT"

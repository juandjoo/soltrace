#!/usr/bin/env bash
# SolTrace 배포 - GitLab + GitHub 동시 push
# 사용법: bash scripts/deploy.sh [브랜치명]  (기본: main)

BRANCH="${1:-main}"
RESULT=0

echo "[1/2] GitLab push (origin/$BRANCH)"
git push origin "$BRANCH" || { echo "WARN: GitLab push 실패"; RESULT=1; }

echo "[2/2] GitHub push (github/$BRANCH)"
git push github "$BRANCH" || { echo "WARN: GitHub push 실패"; RESULT=1; }

[ "$RESULT" -eq 0 ] && echo "Done: $BRANCH → GitLab + GitHub" || echo "Done: $BRANCH (일부 실패 위 경고 확인)"

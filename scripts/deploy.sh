#!/usr/bin/env bash
# SolTrace 배포 - GitLab + GitHub 동시 push
# 사용법: bash scripts/deploy.sh [브랜치명]  (기본: main)

set -euo pipefail

BRANCH="${1:-main}"

echo "[1/2] GitLab push (origin/$BRANCH)"
git push origin "$BRANCH"

echo "[2/2] GitHub push (github/$BRANCH)"
git push github "$BRANCH"

echo "Done: $BRANCH → GitLab + GitHub"

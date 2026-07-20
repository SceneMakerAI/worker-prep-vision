#!/usr/bin/env bash
# prep-vision 자체 업데이트 — GitHub origin/main 최신으로 갱신 후 서비스 재기동.
#
# 사용(배포 서버에서):
#   deploy/update.sh            # 변경 없으면 아무것도 안 하고 종료
#   deploy/update.sh --force    # 변경 없어도 sync + 재기동 강제
#
# 전제:
#   - 배포 디렉토리가 GitHub 를 origin 으로 둔 git clone (최초 1회 부트스트랩은 CLAUDE.md 참조)
#   - .env 는 gitignore(미추적)라 reset --hard 에도 보존된다
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=prep-vision.service
UNIT_SRC="$APP_DIR/deploy/prep-vision.service"
UNIT_DST="/etc/systemd/system/$SERVICE"
BRANCH=main

cd "$APP_DIR"

git fetch origin "$BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
if [[ "$LOCAL" == "$REMOTE" && "${1:-}" != "--force" ]]; then
    echo "이미 최신입니다($(git rev-parse --short HEAD)) — 종료 (강제하려면 --force)"
    exit 0
fi

echo "업데이트: $(git rev-parse --short HEAD) → $(git rev-parse --short "origin/$BRANCH")"
# 서버 로컬 수정을 버리고 원격 main 을 정본으로 강제 일치(.env 등 미추적 파일은 보존)
git reset --hard "origin/$BRANCH"

# 의존성 — uv.lock 그대로 재현(잠금 갱신은 개발 머신 몫)
uv sync --frozen

# systemd 유닛이 저장소 버전과 다르면 갱신(멱등)
if ! cmp -s "$UNIT_SRC" "$UNIT_DST" 2>/dev/null; then
    sudo install -m644 "$UNIT_SRC" "$UNIT_DST"
    sudo systemctl daemon-reload
    echo "systemd 유닛 갱신됨"
fi

sudo systemctl restart "$SERVICE"

# 헬스 확인 — readyz(DB+ffmpeg)까지 최대 30초 대기
PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2 | awk '{print $1}')
PORT=${PORT:-8001}
for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${PORT}/readyz" >/dev/null; then
        echo "배포 완료: $(git rev-parse --short HEAD) / readyz OK (port ${PORT})"
        exit 0
    fi
    sleep 1
done

echo "경고: readyz 무응답 — 'journalctl -u ${SERVICE}' 및 로그 확인 필요" >&2
exit 1

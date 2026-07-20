"""실행 엔트리포인트 — .env(config.Settings)의 APP_HOST/APP_PORT 로 uvicorn 서버를 띄운다.

systemd·로컬 모두 `uv run python src/run.py` 로 기동한다. 바인딩(호스트·포트)을 CLI 인자가
아니라 .env 에서 읽는다 — 설정을 한 곳(config.Settings)에 모으고 포트번호를 소스에 노출하지 않는다.
"""
# 서드파티
import uvicorn

# 로컬
from config import get_settings


def main() -> None:
    """config(.env)의 app_host/app_port 로 app:app 을 서빙한다."""
    settings = get_settings()
    uvicorn.run(
        "app:app",
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,  # 로깅은 app 의 setup_logging 가 구성 — uvicorn 기본과 중복 방지
    )


if __name__ == "__main__":
    main()

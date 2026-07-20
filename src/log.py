"""
로깅 설정과 헬퍼 함수 모음
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s[%(levelname)s] %(filename)s:%(lineno)d | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 앱이 DEBUG 여도 저수준 통신 로그를 도배하는 서드파티 로거 — WARNING 으로 눌러 둔다.
# (httpx 의 "HTTP Request ... 200" INFO 줄은 유용하므로 남긴다. 필요하면 여기 추가.)
_NOISY_LOGGERS = ("httpcore",)


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """
    Summary:
        루트 로거를 1회 구성한다. app 부팅 시점에 호출.
    Args:
        level (str): 로그 레벨 (예: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").
        log_file (str | None): 로그 파일 경로. None 이면 파일 로깅 비활성화.
    Description:
        - 콘솔(stdout)은 항상 출력.
        - log_file 을 주면 같은 포맷으로 파일에도 동시 기록(콘솔+파일).
        - 파일은 RotatingFileHandler 로 10MB×5개 순환 — 무한 증가 방지.
        - _NOISY_LOGGERS(httpcore 등)는 WARNING 으로 눌러 DEBUG 도배를 막는다.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)  # logs/ 자동 생성
        handlers.append(
            RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
        )

    logging.basicConfig(
        level=level.upper(),
        format=_FORMAT,
        datefmt=_DATEFMT,
        handlers=handlers,
        force=True,  # 재호출(reload) 시 핸들러 깨끗이 재구성
    )

    # 서드파티 저수준 통신 로거는 한 단계 눌러 DEBUG 도배 방지(앱 DEBUG 와 무관하게).
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Summary:
        모듈용 로거를 반환한다.
    Args:
        name (str): 로거 이름. 보통 호출 모듈의 __name__.
    Returns:
        logging.Logger: 해당 이름의 로거 인스턴스.
    Description:
        - setup_logging 으로 구성한 루트 로거의 핸들러·포맷을 그대로 상속한다.
        - 모듈마다 get_logger(__name__) 으로 받으면 로그에 모듈명이 찍힌다.
    """
    return logging.getLogger(name)

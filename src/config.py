"""애플리케이션 설정 — .env 에서 로드 (하드코딩 금지).

값 우선순위: 환경변수 > .env(프로젝트 루트) > 아래 기본값.
배포마다 다른 값(미디어 루트·DB 등)은 기본값 없이 필수 → 누락 시 부팅 실패(fail-fast).
검증된 도메인 상수(분할·프레임 정책 기본값)는 여기 두되, 운영자가 .env 로 덮어쓸 수 있게 한다.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트(src/config.py → 부모의 부모). .env 를 절대경로로 고정해
# 실행 CWD 와 무관하게 항상 같은 .env 를 읽는다.
_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 서버 바인딩(API 리슨) ---
    app_host: str = "127.0.0.1"     # 리슨 호스트(배포: 0.0.0.0 또는 사설 IP)
    app_port: int                   # 리슨 포트 — 배포별 필수(.env). 소스에 박지 않음

    # --- 미디어 경로 ---
    # 이 워커는 원본이 있는 호스트에서 실행되어 원본·프레임을 '로컬 파일'로 다룬다(ssh 아님).
    vod_root: str                   # 원본·프레임 루트 — 필수. 원본 = {vod_root}/{v_id}/{file_name}
    frame_root: str = ""            # 프레임 출력 루트(빈값이면 vod_root). agent-vision frame_paths 규칙과 정합

    # --- 장면 분할(scenedetect ContentDetector) — 동적 경계 ---
    prep_threshold: float = 18.0        # 낮을수록 컷 촘촘
    prep_min_sec: float = 1.0           # 세그 최소 길이 — 미만이면 이웃과 병합
    prep_max_sec: float = 30.0          # 세그 최대 길이 — 초과하면 균등분할
    prep_frame_skip: int = 0            # 분할 시 N프레임 건너뛰고 1장 검사(0=전 프레임).
                                        # 속도 ×(N+1), 컷 정밀도 하락 트레이드오프 — 기본 0 권장

    # --- 프레임 추출(ffmpeg CPU) ---
    # 세그당 프레임 수 = max(1, round(길이 × prep_fps)) — 최소 1장 보장(짧은 세그도 대표 1장).
    prep_fps: float = 0.2               # 초당 프레임(0.2 = 5초당 1장)
    prep_scale: str = "none"            # 'none'=원본(native), 예 '1024:768'(추후 축소 옵션)
    prep_jpg_quality: int = 2           # ffmpeg -q:v (1~31, 낮을수록 고화질)
    prep_concurrency: int = 4           # 프레임 추출 ffmpeg 동시 실행 수(CPU)

    # --- DB (MariaDB) ---
    db_ip: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str                        # 실계정명 — 소스에 박지 않음(.env 필수)
    db_pw: str = ""
    db_name: str = "scenemaker"
    db_pool_max: int = 8
    db_pool_recycle: int = 3600         # 유휴 커넥션 재활용(초). NAT 경유 dev 는 짧게(예: 240)

    # --- 로깅 ---
    log_level: str = "INFO"
    log_path: str | None = None

    @property
    def frames_root(self) -> str:
        """프레임 출력 루트 — frame_root 미지정 시 vod_root 로 폴백(원본과 같은 트리에 공존)."""
        return self.frame_root or self.vod_root

    def source_path(self, v_id: int, file_name: str) -> Path:
        """원본 영상 로컬 경로 — {vod_root}/{v_id}/{file_name} (파일명은 prep 요청으로 수신)."""
        return Path(self.vod_root) / str(v_id) / file_name

    def seg_frame_dir(self, v_id: int, seg_id: int) -> Path:
        """세그먼트 프레임 디렉토리 — {frames_root}/{v_id}/seg{seg_id:05d} (agent-vision frame_paths 규칙)."""
        return Path(self.frames_root) / str(v_id) / f"seg{seg_id:05d}"

    def __str__(self) -> str:
        """설정 내용을 [key] = value 로 나열(디버깅·로깅용). 비밀(db_pw)은 마스킹."""
        data = self.model_dump()
        data["db_pw"] = "***" if data.get("db_pw") else ""
        width = max(len(k) for k in data)
        body = "\n".join(f"  [{k:<{width}}] = {v!r}" for k, v in data.items())
        return f"Settings(\n{body}\n)"


@lru_cache
def get_settings() -> Settings:
    """애플리케이션 설정을 로드·캐싱한다. app.py 의 lifespan 에서 1회 호출."""
    return Settings()

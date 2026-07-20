"""API 집계 라우터 — 하위 라우터를 한곳에 모은다.

app.py 는 이 api_router 하나만 등록한다. 새 API = 여기에 include_router 한 줄.
prefix 정책: 헬스 프로브(/healthz·/readyz)는 루트, 비즈니스 API 는 /api/v1 아래.
"""

from fastapi import APIRouter

from api import health, prep

api_router = APIRouter()

# 비즈니스 API — /api/v1
api_router.include_router(prep.router, prefix="/api/v1")

# 인프라 프로브 — 루트
api_router.include_router(health.router)

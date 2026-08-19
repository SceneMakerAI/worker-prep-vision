"""API 집계 라우터 — 하위 라우터를 한곳에 모은다.

app.py 는 이 api_router 하나만 등록한다. 새 API = 여기에 include_router 한 줄.
prefix 정책: 접두사 없음 — 통합 API 문서(POST /prep/segment)가 정본 (2026-08-19 /api/v1 제거).
"""

from fastapi import APIRouter

from api import board, health, prep

api_router = APIRouter()

# 비즈니스 API — 루트 (통합 문서 경로 그대로)
api_router.include_router(prep.router)
api_router.include_router(board.router)

# 인프라 프로브 — 루트
api_router.include_router(health.router)

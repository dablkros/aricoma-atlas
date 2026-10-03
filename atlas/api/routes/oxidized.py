"""Reserved HTTP boundary for future Oxidized orchestration endpoints."""

from fastapi import APIRouter


router = APIRouter(prefix="/oxidized", tags=["oxidized"])

"""
POST /research 요청/응답과 조사 이력 조회 응답 스키마.

응답은 그래프의 분기를 드러낸다:
- 회사명 해결 -> status="ok" + result(생성된 리포트)
- 회사명 미해결 -> status="candidates" + candidates(비슷한 회사명 후보)
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResearchRequest(BaseModel):
    corp_name: str
    question: str


class ResearchRecord(BaseModel):
    id: int
    corp_name: str
    corp_code: str
    question: str
    report: str
    news_mode: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResearchResponse(BaseModel):
    status: Literal["ok", "candidates"]
    result: ResearchRecord | None = None   # status="ok"일 때
    candidates: list[str] | None = None    # status="candidates"일 때

"""
보고서 이력 테이블에 저장되는 데이터의 형태.
POST/research로 리포트가 생성될 때마다 한 건씩 쌓인다.
회사명을 찾지 못한 요청은 저장하지 않는다.
"""

from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base


class Research(Base):
    __tablename__ = "research"

    id: Mapped[int] = mapped_column(primary_key=True)
    corp_name: Mapped[str] = mapped_column(String)
    corp_code: Mapped[str] = mapped_column(String)
    question: Mapped[str] = mapped_column(Text)
    report: Mapped[str] = mapped_column(Text)
    news_mode: Mapped[str] = mapped_column(String)   # "keyword"(A) | "trend"(B)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

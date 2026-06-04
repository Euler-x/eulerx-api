import uuid
from typing import Optional

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import GUID

from app.db.base import Base, TimestampMixin


class AdminConfig(Base, TimestampMixin):
    __tablename__ = "admin_config"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

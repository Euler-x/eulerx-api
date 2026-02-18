import uuid
from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import ContentCategory, ContentType


class LearningContent(Base, TimestampMixin):
    __tablename__ = "learning_content"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[ContentCategory] = mapped_column(
        SAEnum(ContentCategory, name="content_category_enum"), nullable=False
    )
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_type: Mapped[ContentType] = mapped_column(
        SAEnum(ContentType, name="content_type_enum"), nullable=False
    )
    content_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)

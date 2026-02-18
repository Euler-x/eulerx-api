import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import ContentCategory, ContentType


class LearningContentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    category: ContentCategory
    file_path: Optional[str] = None
    content_type: ContentType
    content_url: Optional[str] = None
    display_order: int = 0
    is_published: bool = False


class LearningContentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    category: Optional[ContentCategory] = None
    file_path: Optional[str] = None
    content_type: Optional[ContentType] = None
    content_url: Optional[str] = None
    display_order: Optional[int] = None
    is_published: Optional[bool] = None


class LearningContentResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    category: ContentCategory
    file_path: Optional[str]
    content_type: ContentType
    content_url: Optional[str]
    display_order: int
    is_published: bool
    created_at: datetime

    model_config = {"from_attributes": True}

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SubjectBase(BaseModel):
    title: str
    description: Optional[str] = ""
    color: Optional[str] = "#0284c7"


class SubjectCreate(SubjectBase):
    pass


class SubjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


class SubjectRead(SubjectBase):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

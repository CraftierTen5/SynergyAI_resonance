from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


PolicyLevel = Literal["RED", "YELLOW", "GREEN"]


class PolicyCheck(BaseModel):
    policy_level: str = Field(..., description="RED, YELLOW, or GREEN")


class AssignmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    policy_level: PolicyLevel
    course: str = Field(default="", max_length=200)
    institution_id: str | None = Field(default=None, max_length=100)
    creator_id: str | None = Field(default=None, max_length=100)
    due_date: datetime | None = None


class AssignmentUpdate(BaseModel):
    policy_level: PolicyLevel


class Assignment(AssignmentCreate):
    id: str
    created_at: datetime
    storage: str


class TutorRequest(BaseModel):
    assignment_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=8000)
    language: Literal["english", "hindi", "hinglish"] = "english"
    session_id: str | None = Field(default=None, max_length=100)
    disclosure_acknowledged: bool = False


class VivaRequest(BaseModel):
    assignment_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=500)
    difficulty: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    number_of_questions: int = Field(default=5, ge=1, le=10)
    language: Literal["english", "hindi", "hinglish"] = "english"

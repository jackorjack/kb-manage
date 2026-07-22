"""Pydantic request and response shapes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=8, max_length=256)


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    agentId: str = Field(min_length=1, max_length=256)
    path: Optional[str] = Field(default=None, min_length=1, max_length=4096)


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    path: Optional[str] = Field(default=None, min_length=1, max_length=4096)


class IndexRequest(BaseModel):
    force: bool = False


class UserResponse(BaseModel):
    username: str


class JobResponse(BaseModel):
    id: str
    knowledgeBaseId: str
    kind: str
    mode: str
    phase: str
    status: str
    error: Optional[str] = None
    exitCode: Optional[int] = None
    createdAt: str
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    files: List[Dict[str, Any]] = []

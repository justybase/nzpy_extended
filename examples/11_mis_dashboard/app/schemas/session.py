"""Pydantic models for the simulated session API (role-based access)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UserInfo(BaseModel):
    code: str
    name: str
    role: str
    role_label: str
    advisor_id: int | None = None
    branch_id: int | None = None
    region_id: int | None = None


class SessionResponse(BaseModel):
    user: UserInfo


class SwitchUserRequest(BaseModel):
    code: str


class UserListEntry(BaseModel):
    code: str
    name: str
    role: str
    role_label: str


class UsersResponse(BaseModel):
    users: list[UserListEntry] = Field(default_factory=list)
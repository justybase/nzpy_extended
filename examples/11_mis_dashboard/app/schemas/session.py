"""Pydantic models for authentication and role-based session APIs."""

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
    authenticated_username: str | None = None
    authenticated_user_code: str | None = None
    is_impersonating: bool = False
    can_switch_persona: bool = False
    can_refresh_cache: bool = False
    can_view_global_quality: bool = False


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


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)

"""The signed-in user, as passed around the UI and services."""

from __future__ import annotations

from dataclasses import dataclass

from aurum_lims.security.roles import Permission, Role, has_permission


@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    full_name: str
    role: Role

    @classmethod
    def from_user(cls, user) -> SessionUser:
        return cls(id=user.id, username=user.username, full_name=user.full_name, role=Role(user.role))

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

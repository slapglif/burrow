from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PermissionState:
    view: bool = True
    control: bool = False
    clipboard: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PermissionState":
        payload = payload or {}
        readonly = bool(payload.get("readonly", False))
        return cls(
            view=bool(payload.get("view", True)),
            control=False if readonly else bool(payload.get("control", False)),
            clipboard=bool(payload.get("clipboard", False)),
        )


@dataclass
class PermissionTransition:
    previous: PermissionState = field(default_factory=PermissionState)
    current: PermissionState = field(default_factory=PermissionState)
    actor: str = ""
    reason: str = ""
    requested: dict[str, Any] = field(default_factory=dict)
    at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous": self.previous.to_dict(),
            "current": self.current.to_dict(),
            "actor": self.actor,
            "reason": self.reason,
            "requested": dict(self.requested),
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PermissionTransition | None":
        if not payload:
            return None
        return cls(
            previous=PermissionState.from_dict(payload.get("previous") or payload.get("before")),
            current=PermissionState.from_dict(payload.get("current") or payload.get("after")),
            actor=payload.get("actor", ""),
            reason=payload.get("reason", ""),
            requested=dict(payload.get("requested", {})),
            at=payload.get("at"),
        )


@dataclass
class ReconnectState:
    supported: bool = False
    resume_token: str = ""
    epoch: int = 0
    strategy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReconnectState":
        payload = payload or {}
        return cls(
            supported=bool(payload.get("supported", False)),
            resume_token=payload.get("resume_token", ""),
            epoch=int(payload.get("epoch", 0) or 0),
            strategy=payload.get("strategy", ""),
        )


@dataclass
class PrivacyState:
    supported: bool = False
    enabled: bool = False
    mode: str = ""
    local_input_blocked: bool = False
    local_screen_blank: bool = False
    local_audio_muted: bool = False
    stubbed: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PrivacyState":
        payload = payload or {}
        return cls(
            supported=bool(payload.get("supported", False)),
            enabled=bool(payload.get("enabled", False)),
            mode=payload.get("mode", ""),
            local_input_blocked=bool(payload.get("local_input_blocked", False)),
            local_screen_blank=bool(payload.get("local_screen_blank", False)),
            local_audio_muted=bool(payload.get("local_audio_muted", False)),
            stubbed=bool(payload.get("stubbed", False)),
            note=payload.get("note", ""),
        )


@dataclass
class DesktopTarget:
    kind: str
    id: str
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "DesktopTarget | None":
        if not payload:
            return None
        return cls(kind=payload["kind"], id=payload["id"], title=payload.get("title", ""))


@dataclass
class DesktopFrame:
    session_id: str
    mime_type: str
    data_base64: str
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DesktopFrame":
        return cls(**payload)


@dataclass
class DesktopSession:
    session_id: str
    peer: str
    backend: str
    state: str
    owner: str = ""
    controller: str = ""
    created_at: float | None = None
    updated_at: float | None = None
    last_error: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    viewer: dict[str, Any] = field(default_factory=dict)
    computer_use: dict[str, Any] = field(default_factory=dict)
    permissions: PermissionState = field(default_factory=PermissionState)
    permission_revision: int = 0
    permission_transition: PermissionTransition | None = None
    reconnect: ReconnectState = field(default_factory=ReconnectState)
    privacy: PrivacyState = field(default_factory=PrivacyState)
    target: DesktopTarget | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["permissions"] = self.permissions.to_dict()
        payload["permission_transition"] = (
            self.permission_transition.to_dict() if self.permission_transition else None
        )
        payload["reconnect"] = self.reconnect.to_dict()
        payload["privacy"] = self.privacy.to_dict()
        payload["target"] = self.target.to_dict() if self.target else None
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DesktopSession":
        return cls(
            session_id=payload["session_id"],
            peer=payload["peer"],
            backend=payload["backend"],
            state=payload["state"],
            owner=payload.get("owner", ""),
            controller=payload.get("controller", ""),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            last_error=payload.get("last_error", ""),
            capabilities=payload.get("capabilities", {}),
            viewer=payload.get("viewer", {}),
            computer_use=payload.get("computer_use", {}),
            permissions=PermissionState.from_dict(payload.get("permissions")),
            permission_revision=int(payload.get("permission_revision", 0) or 0),
            permission_transition=PermissionTransition.from_dict(payload.get("permission_transition")),
            reconnect=ReconnectState.from_dict(payload.get("reconnect")),
            privacy=PrivacyState.from_dict(payload.get("privacy")),
            target=DesktopTarget.from_dict(payload.get("target")),
        )

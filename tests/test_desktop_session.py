from burrow.desktop_session import (
    DesktopFrame,
    DesktopSession,
    DesktopTarget,
    PermissionState,
    PermissionTransition,
    PrivacyState,
    ReconnectState,
)


def test_desktop_session_round_trip():
    session = DesktopSession(
        session_id="sess-1",
        peer="peer-1",
        backend="xpra",
        state="ready",
        capabilities={"clipboard": True},
        viewer={"local_port": 14500},
        computer_use={"snapshot": True},
        permissions=PermissionState(view=True, control=True, clipboard=False),
        permission_revision=2,
        permission_transition=PermissionTransition(
            previous=PermissionState(view=True, control=False, clipboard=False),
            current=PermissionState(view=True, control=True, clipboard=False),
            actor="peer-1",
            reason="operator approved control",
            requested={"control": True},
            at=12.5,
        ),
        reconnect=ReconnectState(
            supported=True,
            resume_token="resume-1",
            epoch=4,
            strategy="resume-or-reopen",
        ),
        privacy=PrivacyState(
            supported=True,
            enabled=False,
            mode="query",
            local_input_blocked=False,
            local_screen_blank=False,
            stubbed=True,
            note="privacy mode not yet applied",
        ),
        target=DesktopTarget(kind="display", id="0", title="Primary Display"),
    )

    payload = session.to_dict()
    rebuilt = DesktopSession.from_dict(payload)

    assert payload["session_id"] == "sess-1"
    assert rebuilt.permissions.control is True
    assert rebuilt.permission_revision == 2
    assert rebuilt.permission_transition is not None
    assert rebuilt.permission_transition.previous.control is False
    assert rebuilt.reconnect.resume_token == "resume-1"
    assert rebuilt.privacy.stubbed is True
    assert rebuilt.target.title == "Primary Display"


def test_permission_state_honors_readonly_flag():
    state = PermissionState.from_dict({"view": True, "control": True, "clipboard": True, "readonly": True})

    assert state.view is True
    assert state.control is False
    assert state.clipboard is True


def test_desktop_frame_round_trip():
    frame = DesktopFrame(
        session_id="sess-1",
        mime_type="image/png",
        data_base64="YWJj",
        width=100,
        height=50,
    )

    payload = frame.to_dict()
    rebuilt = DesktopFrame.from_dict(payload)

    assert rebuilt.width == 100
    assert rebuilt.mime_type == "image/png"

import asyncio

import pytest

from burrow import protocol
from burrow.peer import OptionalFeatureUnsupported, Peer


@pytest.mark.asyncio
async def test_peer_marks_optional_type_unsupported_from_unknown_type_error():
    peer = Peer("ws://example.test", "alice")
    fut = asyncio.get_running_loop().create_future()
    peer._optional_probe_waiters[protocol.GROUP_JOIN] = [fut]

    peer._handle_error(protocol.error("unknown type: group_join"))

    assert protocol.GROUP_JOIN in peer._unsupported_optional_types
    assert fut.done() is True
    assert fut.result() is False


@pytest.mark.asyncio
async def test_join_group_and_update_status_return_false_without_noisy_output(monkeypatch, capsys):
    peer = Peer("ws://example.test", "alice")
    peer.ws = object()
    peer._listen_ready.set()
    sent_types = []

    async def fake_send(msg):
        sent_types.append(msg["type"])
        peer._handle_error(protocol.error(f"unknown type: {msg['type']}"))

    monkeypatch.setattr(peer, "_send", fake_send)

    assert await peer.join_group("agent-pool") is False
    assert "agent-pool" not in peer.groups
    assert await peer.update_status("busy", "testing") is False
    assert protocol.GROUP_JOIN in peer._unsupported_optional_types
    assert protocol.STATUS_UPDATE in peer._unsupported_optional_types

    sent_before_cache = list(sent_types)
    assert await peer.join_group("agent-pool") is False
    assert await peer.update_status("idle") is False
    assert sent_types == sent_before_cache

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.asyncio
async def test_optional_group_requests_raise_explicit_unsupported(monkeypatch):
    peer = Peer("ws://example.test", "alice")
    peer.ws = object()
    peer._listen_ready.set()
    sent_types = []

    async def fake_send(msg):
        sent_types.append(msg["type"])
        peer._handle_error(protocol.error(f"unknown type: {msg['type']}"))

    monkeypatch.setattr(peer, "_send", fake_send)

    with pytest.raises(OptionalFeatureUnsupported, match="group_list"):
        await peer.list_groups()
    with pytest.raises(OptionalFeatureUnsupported, match="group_members"):
        await peer.get_group_members("agent-pool")

    sent_before_cache = list(sent_types)
    with pytest.raises(OptionalFeatureUnsupported, match="group_list"):
        await peer.list_groups()
    with pytest.raises(OptionalFeatureUnsupported, match="group_members"):
        await peer.get_group_members("agent-pool")
    assert sent_types == sent_before_cache


@pytest.mark.asyncio
async def test_request_response_helpers_require_listen_loop():
    peer = Peer("ws://example.test", "alice")
    peer.ws = object()

    with pytest.raises(RuntimeError, match="active listen loop"):
        await peer.request_peers()

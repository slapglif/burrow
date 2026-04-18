import sys
import types

import pytest

if "mcp.server.fastmcp" not in sys.modules:
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(func):
                return func
            return decorator

    fastmcp_module.FastMCP = FastMCP
    sys.modules["mcp"] = types.ModuleType("mcp")
    sys.modules["mcp.server"] = types.ModuleType("mcp.server")
    sys.modules["mcp.server.fastmcp"] = fastmcp_module

from burrow import mcp_server
from burrow.peer import OptionalFeatureUnsupported


class FakePeer:
    def __init__(self):
        self.groups = set()
        self.join_attempts = []
        self.status_attempts = []
        self.group_messages = []
        self.group_list_attempts = 0
        self.group_member_attempts = []

    async def join_group(self, group):
        self.join_attempts.append(group)
        return False

    async def update_status(self, status, task=""):
        self.status_attempts.append((status, task))
        return False

    async def send_group_message(self, group, body, wait_ack=True):
        self.group_messages.append((group, body, wait_ack))
        return "delivered"

    async def list_groups(self):
        self.group_list_attempts += 1
        raise OptionalFeatureUnsupported("group_list")

    async def get_group_members(self, group):
        self.group_member_attempts.append(group)
        raise OptionalFeatureUnsupported("group_members")


async def _return_peer(peer):
    return peer


@pytest.mark.asyncio
async def test_mcp_tools_report_registry_compatibility_fallbacks(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    join_result = await mcp_server.burrow_join_group("agent-pool")
    status_result = await mcp_server.burrow_update_status("busy", "testing")
    group_message_result = await mcp_server.burrow_group_message("agent-pool", "hello")
    group_list_result = await mcp_server.burrow_list_groups()
    group_members_result = await mcp_server.burrow_group_members("agent-pool")

    assert join_result == mcp_server.GROUPS_UNSUPPORTED
    assert status_result == mcp_server.STATUS_UNSUPPORTED
    assert group_message_result == mcp_server.GROUPS_UNSUPPORTED
    assert group_list_result == mcp_server.GROUPS_UNSUPPORTED
    assert group_members_result == mcp_server.GROUPS_UNSUPPORTED
    assert peer.join_attempts == ["agent-pool", "agent-pool"]
    assert peer.status_attempts == [("busy", "testing")]
    assert peer.group_messages == []
    assert peer.group_list_attempts == 1
    assert peer.group_member_attempts == ["agent-pool"]

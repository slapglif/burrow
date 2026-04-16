package com.nila.burrow.remote.model

import org.json.JSONArray
import org.json.JSONObject

/**
 * Compact data models that mirror Burrow desktop protocol payloads.
 */

data class PeerInfo(
    val id: String,
    val name: String,
)

sealed interface BurrowEvent {
    data class PeersChanged(val peers: List<PeerInfo>) : BurrowEvent
    data class DesktopSessionReady(val session: BurrowDesktopSession) : BurrowEvent
    data class DesktopSessionClosed(val sessionId: String, val note: String? = null) : BurrowEvent
    data class DesktopFrameAvailable(val frame: BurrowDesktopFrame) : BurrowEvent
    data class DesktopPermission(val sessionId: String, val permission: JSONObject) : BurrowEvent
    data class Error(val message: String) : BurrowEvent
    data class ConnectionState(val connected: Boolean, val message: String = "") : BurrowEvent
}

data class BurrowDesktopViewer(
    val protocol: String? = null,
    val remotePort: Int? = null,
    val localPort: Int? = null,
    val viewerUrl: String? = null,
)

data class BurrowDesktopSession(
    val sessionId: String,
    val peer: String? = null,
    val backend: String? = null,
    val state: String = "ready",
    val width: Int? = null,
    val height: Int? = null,
    val viewer: BurrowDesktopViewer = BurrowDesktopViewer(),
)

data class BurrowDesktopFrame(
    val sessionId: String,
    val mimeType: String,
    val dataBase64: String,
    val width: Int = 0,
    val height: Int = 0,
    val stubbed: Boolean = false,
)

internal const val CMD_REGISTER = "register"
internal const val CMD_PING = "ping"
internal const val CMD_PONG = "pong"

internal const val MSG_REGISTERED = "registered"
internal const val MSG_PEERS = "peers"
internal const val MSG_PEER_JOINED = "peer_joined"
internal const val MSG_PEER_LEFT = "peer_left"

internal const val CMD_DESKTOP_SESSION_OPEN = "desktop_session_open"
internal const val CMD_DESKTOP_SESSION_CLOSE = "desktop_session_close"
internal const val CMD_DESKTOP_FRAME_REQUEST = "desktop_frame_request"
internal const val CMD_DESKTOP_INPUT = "desktop_input"
internal const val CMD_DESKTOP_PRIVACY = "desktop_privacy"

internal const val MSG_DESKTOP_SESSION_READY = "desktop_session_ready"
internal const val MSG_DESKTOP_SESSION_CLOSE = "desktop_session_close"
internal const val MSG_DESKTOP_SESSION_LIST = "desktop_session_list"
internal const val MSG_DESKTOP_FRAME = "desktop_frame"
internal const val MSG_DESKTOP_PERMISSION = "desktop_permission"

internal const val MSG_ERROR = "error"

internal fun peersFromJsonArray(peers: JSONArray): List<PeerInfo> {
    val result = ArrayList<PeerInfo>(peers.length())
    for (i in 0 until peers.length()) {
        val raw = peers.optJSONObject(i) ?: continue
        val id = raw.optString("id", "").trim()
        if (id.isNotBlank()) {
            val name = raw.optString("name", id)
            result.add(PeerInfo(id = id, name = name))
        }
    }
    return result
}

internal fun JSONObject.toPeerInfoList(): List<PeerInfo> =
    this.optJSONArray("peers")?.let(::peersFromJsonArray) ?: emptyList()

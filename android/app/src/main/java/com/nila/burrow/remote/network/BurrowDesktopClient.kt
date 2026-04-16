package com.nila.burrow.remote.network

import android.util.Log
import com.nila.burrow.remote.model.BurrowDesktopFrame
import com.nila.burrow.remote.model.BurrowDesktopSession
import com.nila.burrow.remote.model.BurrowEvent
import com.nila.burrow.remote.model.CMD_DESKTOP_FRAME_REQUEST
import com.nila.burrow.remote.model.CMD_DESKTOP_INPUT
import com.nila.burrow.remote.model.CMD_DESKTOP_PRIVACY
import com.nila.burrow.remote.model.CMD_DESKTOP_SESSION_CLOSE
import com.nila.burrow.remote.model.CMD_DESKTOP_SESSION_OPEN
import com.nila.burrow.remote.model.CMD_PING
import com.nila.burrow.remote.model.CMD_PONG
import com.nila.burrow.remote.model.CMD_REGISTER
import com.nila.burrow.remote.model.MSG_DESKTOP_FRAME
import com.nila.burrow.remote.model.MSG_DESKTOP_PERMISSION
import com.nila.burrow.remote.model.MSG_DESKTOP_SESSION_CLOSE
import com.nila.burrow.remote.model.MSG_DESKTOP_SESSION_READY
import com.nila.burrow.remote.model.MSG_ERROR
import com.nila.burrow.remote.model.MSG_PEER_JOINED
import com.nila.burrow.remote.model.MSG_PEER_LEFT
import com.nila.burrow.remote.model.MSG_PEERS
import com.nila.burrow.remote.model.MSG_REGISTERED
import com.nila.burrow.remote.model.PeerInfo
import com.nila.burrow.remote.model.toPeerInfoList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONException
import org.json.JSONObject
import kotlin.math.min
import kotlin.random.Random
import java.util.LinkedHashMap
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class BurrowDesktopClient {
    private val client = OkHttpClient()
    private var socket: WebSocket? = null
    private var registryUri: String = ""
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var heartbeatJob: Job? = null
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private var manualDisconnect = false

    private val events = MutableSharedFlow<BurrowEvent>(
        replay = 0,
        extraBufferCapacity = 128,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    private val pendingSessions = ConcurrentHashMap<String, PeerState>()
    private val peersById = LinkedHashMap<String, PeerInfo>()

    private var localPeerName: String = ""
    private var localPeerId: String = ""

    fun events(): SharedFlow<BurrowEvent> = events

    fun isConnected(): Boolean = socket != null

    fun connect(uri: String, peerName: String) {
        if (socket != null) {
            return
        }

        val trimmedUri = uri.trim()
        if (trimmedUri.isBlank()) {
            emitEvent(BurrowEvent.Error("server uri is empty"))
            return
        }

        registryUri = trimmedUri
        localPeerName = peerName.ifBlank { "burrow-android" }
        manualDisconnect = false
        reconnectAttempt = 0
        stopReconnectLoop()
        emitEvent(BurrowEvent.ConnectionState(connected = true, message = "connecting"))
        socket = client.newWebSocket(Request.Builder().url(trimmedUri).build(), createListener())
    }

    fun disconnect() {
        manualDisconnect = true
        stopReconnectLoop()
        stopHeartbeat()
        socket?.close(1000, "user disconnect")
        socket = null
        pendingSessions.clear()
        peersById.clear()
        localPeerId = ""
        emitEvent(BurrowEvent.ConnectionState(connected = false, message = "disconnected"))
    }

    fun currentPeers(): List<PeerInfo> = peersById.values.toList()

    fun openSession(peerId: String, backend: String = "auto", readonly: Boolean = false): String {
        val sessionId = UUID.randomUUID().toString().replace("-", "").take(12)
        val payload = JSONObject().apply {
            put("type", CMD_DESKTOP_SESSION_OPEN)
            put("to", peerId)
            put("session_id", sessionId)
            put("backend", backend)
            put("readonly", readonly)
            put("remote_port", 0)
        }
        pendingSessions[sessionId] = PeerState(peerId = peerId, localSessionId = sessionId)
        send(payload)
        return sessionId
    }

    fun requestFrame(peerId: String, sessionId: String) {
        send(
            JSONObject().apply {
                put("type", CMD_DESKTOP_FRAME_REQUEST)
                put("to", peerId)
                put("session_id", sessionId)
            },
        )
    }

    fun sendInput(peerId: String, sessionId: String, action: JSONObject) {
        send(
            JSONObject().apply {
                put("type", CMD_DESKTOP_INPUT)
                put("to", peerId)
                put("session_id", sessionId)
                put("action", action)
            },
        )
    }

    fun closeSession(peerId: String, sessionId: String) {
        send(
            JSONObject().apply {
                put("type", CMD_DESKTOP_SESSION_CLOSE)
                put("to", peerId)
                put("session_id", sessionId)
            },
        )
        pendingSessions.remove(sessionId)
    }

    fun setPrivacy(peerId: String, sessionId: String, enabled: Boolean) {
        send(
            JSONObject().apply {
                put("type", CMD_DESKTOP_PRIVACY)
                put("to", peerId)
                put("session_id", sessionId)
                put("privacy", JSONObject().apply { put("enabled", enabled) })
            },
        )
    }

    fun sessionState(sessionId: String): PeerState? = pendingSessions[sessionId]

    private fun createListener(): WebSocketListener =
        object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                reconnectAttempt = 0
                stopReconnectLoop()
                emitEvent(BurrowEvent.ConnectionState(connected = true, message = "connected"))
                sendRegister()
                startHeartbeat()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleMessage(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                stopHeartbeat()
                emitEvent(BurrowEvent.ConnectionState(connected = false, message = "failure"))
                emitEvent(BurrowEvent.Error("websocket failure: ${t.message ?: "connection error"}"))
                socket = null
                if (!manualDisconnect) {
                    scheduleReconnect()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                stopHeartbeat()
                emitEvent(BurrowEvent.ConnectionState(connected = false, message = "closed: $reason"))
                socket = null
                if (!manualDisconnect && reason != "user disconnect") {
                    emitEvent(BurrowEvent.Error("websocket closed: $reason"))
                    scheduleReconnect()
                }
            }
        }

    private fun handleMessage(raw: String) {
        try {
            val data = JSONObject(raw)
            when (val type = data.optString("type", "")) {
                CMD_PONG -> return
                MSG_REGISTERED -> {
                    localPeerId = data.optString("id", "")
                    emitPeers(data.toPeerInfoList())
                    emitEvent(BurrowEvent.ConnectionState(connected = true, message = "registered"))
                }
                MSG_PEERS -> {
                    emitPeers(data.toPeerInfoList())
                    emitEvent(BurrowEvent.ConnectionState(connected = true, message = "peer sync"))
                }
                MSG_PEER_JOINED -> {
                    val id = data.optString("id", "").trim()
                    if (id.isNotBlank()) {
                        val name = data.optString("name", id)
                        peersById[id] = PeerInfo(id = id, name = name)
                        emitEvent(BurrowEvent.PeersChanged(peersById.values.toList()))
                    }
                }
                MSG_PEER_LEFT -> {
                    val id = data.optString("id", "").trim()
                    if (id.isNotBlank()) {
                        peersById.remove(id)
                        emitEvent(BurrowEvent.PeersChanged(peersById.values.toList()))
                    }
                }
                CMD_PING -> send(JSONObject().apply { put("type", CMD_PONG) })
                MSG_ERROR -> emitEvent(BurrowEvent.Error(data.optString("message", "remote error")))
                MSG_DESKTOP_SESSION_READY -> {
                    val sessionId = data.optString("session_id", "")
                    if (sessionId.isBlank()) {
                        return
                    }
                    val sessionObj = data.optJSONObject("session") ?: JSONObject()
                    val peerId = data.optString("from", sessionObj.optString("peer", ""))

                    val session = BurrowDesktopSession(
                        sessionId = sessionId,
                        peer = peerId.ifBlank { data.optString("to", "") }.ifBlank { null },
                        backend = sessionObj.optString("backend", "").ifBlank { null },
                        state = sessionObj.optString("state", "ready"),
                        width = sessionObj.optInt("width", 0).takeIf { it > 0 },
                        height = sessionObj.optInt("height", 0).takeIf { it > 0 },
                    )
                    val state = pendingSessions.getOrPut(sessionId) {
                        PeerState(peerId = peerId.ifBlank { localPeerId }, localSessionId = sessionId)
                    }
                    state.current = session

                    emitEvent(BurrowEvent.DesktopSessionReady(session))
                }
                MSG_DESKTOP_SESSION_CLOSE -> {
                    val sessionId = data.optString("session_id", "")
                    if (sessionId.isNotBlank()) {
                        pendingSessions.remove(sessionId)
                        emitEvent(BurrowEvent.DesktopSessionClosed(sessionId, data.optString("error", "").ifBlank { null }))
                    }
                }
                MSG_DESKTOP_FRAME -> {
                    val frameJson = data.optJSONObject("frame") ?: return
                    emitEvent(
                        BurrowEvent.DesktopFrameAvailable(
                            BurrowDesktopFrame(
                                sessionId = frameJson.optString("session_id", data.optString("session_id", "")),
                                mimeType = frameJson.optString("mime_type", "image/png"),
                                dataBase64 = frameJson.optString("data_base64", ""),
                                width = frameJson.optInt("width", 0),
                                height = frameJson.optInt("height", 0),
                                stubbed = frameJson.optBoolean("stubbed", false),
                            ),
                        ),
                    )
                }
                MSG_DESKTOP_PERMISSION -> {
                    emitEvent(
                        BurrowEvent.DesktopPermission(
                            sessionId = data.optString("session_id", ""),
                            permission = data.optJSONObject("permission") ?: JSONObject(),
                        ),
                    )
                }
                else -> {
                    if (type.isNotBlank()) {
                        emitEvent(BurrowEvent.Error("Unhandled message: $type"))
                    }
                }
            }
        } catch (exception: JSONException) {
            emitEvent(BurrowEvent.Error("Malformed message from server"))
            Log.w(TAG, "Invalid JSON from Burrow socket: $raw", exception)
        }
    }

    private fun sendRegister() {
        send(
            JSONObject().apply {
                put("type", CMD_REGISTER)
                put("name", localPeerName)
            },
        )
    }

    private fun send(payload: JSONObject): Boolean {
        val active = socket ?: run {
            emitEvent(BurrowEvent.Error("socket not connected"))
            return false
        }
        val accepted = active.send(payload.toString())
        if (!accepted) {
            emitEvent(BurrowEvent.Error("failed to queue message"))
        }
        return accepted
    }

    private fun startHeartbeat() {
        stopHeartbeat()
        heartbeatJob = scope.launch {
            while (socket != null) {
                send(JSONObject().apply { put("type", CMD_PING) })
                delay(15_000)
            }
        }
    }

    private fun stopHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = null
    }

    private fun scheduleReconnect() {
        if (manualDisconnect || registryUri.isBlank()) {
            return
        }

        if (reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
            emitEvent(BurrowEvent.Error("reconnect limit reached ($MAX_RECONNECT_ATTEMPTS)"))
            return
        }

        reconnectAttempt += 1
        val delayMs = computeReconnectDelayMs(reconnectAttempt)
        emitEvent(BurrowEvent.ConnectionState(connected = false, message = "reconnecting in ${delayMs}ms (attempt $reconnectAttempt/$MAX_RECONNECT_ATTEMPTS)"))
        stopReconnectLoop()
        reconnectJob = scope.launch {
            delay(delayMs)
            if (!manualDisconnect && socket == null) {
                connect(registryUri, localPeerName)
            }
        }
    }

    private fun computeReconnectDelayMs(attempt: Int): Long {
        val cappedAttempt = attempt.coerceIn(1, 12)
        val exponentialDelay = RECONNECT_INITIAL_DELAY_MS * (1L shl (cappedAttempt - 1))
        val jitterMs = Random.nextLong(0L, RECONNECT_JITTER_WINDOW_MS + 1L)
        return (exponentialDelay + jitterMs).coerceAtMost(RECONNECT_MAX_DELAY_MS)
    }

    private fun stopReconnectLoop() {
        reconnectJob?.cancel()
        reconnectJob = null
        reconnectAttempt = 0
    }

    private fun emitPeers(peers: List<PeerInfo>) {
        peersById.clear()
        peers.forEach { peersById[it.id] = it }
        emitEvent(BurrowEvent.PeersChanged(peersById.values.toList()))
    }

    private fun emitEvent(event: BurrowEvent) {
        events.tryEmit(event)
    }

    companion object {
        private const val TAG = "BurrowDesktopClient"

        private const val MAX_RECONNECT_ATTEMPTS = 16
        private const val RECONNECT_INITIAL_DELAY_MS = 500L
        private const val RECONNECT_JITTER_WINDOW_MS = 250L
        private const val RECONNECT_MAX_DELAY_MS = 30_000L
    }
}


data class PeerState(
    val peerId: String,
    val localSessionId: String,
    var current: BurrowDesktopSession? = null,
)

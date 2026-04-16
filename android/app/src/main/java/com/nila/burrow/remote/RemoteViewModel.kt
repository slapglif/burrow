package com.nila.burrow.remote

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nila.burrow.remote.model.BurrowDesktopFrame
import com.nila.burrow.remote.model.BurrowDesktopSession
import com.nila.burrow.remote.model.BurrowEvent
import com.nila.burrow.remote.model.PeerInfo
import com.nila.burrow.remote.network.BurrowDesktopClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.max
import java.util.ArrayDeque

class BurrowRemoteViewModel(
    private val client: BurrowDesktopClient = BurrowDesktopClient(),
) : ViewModel() {

    companion object {
        private const val DEFAULT_STREAM_REQUEST_DELAY_MS = 120L
        private const val MIN_STREAM_REQUEST_DELAY_MS = 35L
        private const val MAX_STREAM_REQUEST_DELAY_MS = 500L

        private const val FRAME_GAP_EWMA_ALPHA_DIVISOR = 5L
        private const val FRAME_STALE_WARNING_MS = 1200L
        private const val FRAME_STALE_ERROR_MS = 2800L

        private const val INPUT_FLUSH_WINDOW_MS = 12L
        private const val INPUT_BATCH_MAX = 24
        private const val INPUT_QUEUE_MAX = 120
        private const val INPUT_SCROLL_STEP_BOUNDS = 240
        private const val FRAME_HEALTH_POLL_MS = 600L

        private const val ACTION_MOVE = "move"
        private const val ACTION_SCROLL = "scroll"
    }

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state

    private var framePumpRunning: Boolean = false
    private var framePumpJob: Job? = null
    private var frameHealthJob: Job? = null

    private var inputPumpRunning: Boolean = false
    private var inputPumpJob: Job? = null

    private val inputSync = Any()
    private val inputQueue = ArrayDeque<JSONObject>()
    private var pendingMouseMove: JSONObject? = null
    private var pendingScrollX = 0
    private var pendingScrollY = 0

    private var streamDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS
    private var smoothedGapMs = 0L
    private var lastFrameWallClockMs = 0L

    init {
        viewModelScope.launch {
            client.events().collect { event ->
                when (event) {
                    is BurrowEvent.ConnectionState -> {
                        _state.update { current ->
                            if (event.connected) {
                                current.copy(connected = true, status = event.message, isError = false)
                            } else {
                                stopFramePump()
                                stopInputPump()
                                stopFrameHealthMonitor()
                                val isTransient = event.message == "connecting" ||
                                    event.message == "disconnected" ||
                                    event.message.startsWith("closed:") ||
                                    event.message.startsWith("reconnecting in") ||
                                    event.message == "failure"
                                current.copy(
                                    connected = false,
                                    peers = emptyList(),
                                    selectedPeerId = "",
                                    status = event.message,
                                    isError = !isTransient,
                                    activeSessionId = "",
                                    activeSession = null,
                                    activeSessionPeerId = "",
                                    imageBitmap = null,
                                    frameWidth = 0,
                                    frameHeight = 0,
                                    frameSeq = 0,
                                    lastFrameAt = 0L,
                                    lastFrameStubbed = false,
                                    streamRequestDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS,
                                    streamFrameGapMs = 0L,
                                    streamJitterMs = 0L,
                                    streamFrameStale = false,
                                    streamStaleMs = 0L,
                                    permissionState = null,
                                )
                            }
                        }
                        resetFrameStreamState()
                    }

                    is BurrowEvent.Error -> {
                        _state.update { it.copy(status = event.message, isError = true) }
                    }
                    is BurrowEvent.PeersChanged -> {
                        _state.update { current ->
                            val stillSelected = event.peers.any { it.id == current.selectedPeerId }
                            current.copy(
                                peers = event.peers,
                                selectedPeerId = if (stillSelected) current.selectedPeerId else "",
                                status = "connected",
                                isError = false,
                            )
                        }
                    }

                    is BurrowEvent.DesktopSessionReady -> {
                        onSessionReady(event.session)
                    }

                    is BurrowEvent.DesktopSessionClosed -> {
                        if (_state.value.activeSessionId == event.sessionId) {
                            _state.update {
                                it.copy(
                                    activeSessionId = "",
                                    activeSession = null,
                                    activeSessionPeerId = "",
                                    imageBitmap = null,
                                    frameWidth = 0,
                                    frameHeight = 0,
                                    frameSeq = 0,
                                    lastFrameAt = 0L,
                                    lastFrameStubbed = false,
                                    streamRequestDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS,
                                    streamFrameGapMs = 0L,
                                    streamJitterMs = 0L,
                                    streamFrameStale = false,
                                    streamStaleMs = 0L,
                                    status = event.note?.let { "session closed: $it" } ?: "session closed",
                                    isError = false,
                                    permissionState = null,
                                )
                            }
                            stopFramePump()
                            stopInputPump()
                            stopFrameHealthMonitor()
                            resetFrameStreamState()
                        }
                    }

                    is BurrowEvent.DesktopFrameAvailable -> {
                        onFrame(event.frame)
                    }

                    is BurrowEvent.DesktopPermission -> {
                        _state.update {
                            it.copy(
                                status = "permission event",
                                isError = false,
                                permissionState = event.permission.toString(),
                            )
                        }
                    }
                }
            }
        }
    }

    fun onServerUriChange(uri: String) {
        _state.update { it.copy(serverUri = uri) }
    }

    fun onPeerNameChange(peerName: String) {
        _state.update { it.copy(peerName = peerName) }
    }

    fun toggleConnection() {
        if (_state.value.connected) {
            client.disconnect()
            _state.update {
                UiState(serverUri = it.serverUri, peerName = it.peerName)
            }
            stopFramePump()
            stopInputPump()
            stopFrameHealthMonitor()
            resetFrameStreamState()
            return
        }

        client.connect(_state.value.serverUri, _state.value.peerName)
        _state.update {
            it.copy(status = "connecting", isError = false)
        }
    }

    fun selectPeer(peerId: String) {
        _state.update { it.copy(selectedPeerId = peerId) }
    }

    fun openSelectedSession() {
        val targetPeerId = _state.value.selectedPeerId
        if (targetPeerId.isBlank() || !_state.value.connected) {
            _state.update { it.copy(status = "select peer first", isError = true) }
            return
        }

        val sessionId = client.openSession(targetPeerId)
        resetFrameStreamState()
        _state.update {
            it.copy(
                activeSessionId = sessionId,
                activeSessionPeerId = targetPeerId,
                frameWidth = 0,
                frameHeight = 0,
                frameSeq = 0,
                lastFrameAt = 0L,
                lastFrameStubbed = false,
                streamRequestDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS,
                streamFrameGapMs = 0L,
                streamJitterMs = 0L,
                streamFrameStale = true,
                streamStaleMs = 0L,
                status = "opening session",
                isError = false,
                permissionState = null,
            )
        }
        client.requestFrame(targetPeerId, sessionId)
        startFramePump(targetPeerId, sessionId)
        startFrameHealthMonitor(targetPeerId, sessionId)
    }

    fun closeActiveSession() {
        val active = _state.value
        if (active.activeSessionId.isBlank() || active.activeSessionPeerId.isBlank()) {
            return
        }
        client.closeSession(active.activeSessionPeerId, active.activeSessionId)
        _state.update {
            it.copy(
                activeSessionId = "",
                activeSession = null,
                activeSessionPeerId = "",
                imageBitmap = null,
                frameWidth = 0,
                frameHeight = 0,
                frameSeq = 0,
                lastFrameAt = 0L,
                lastFrameStubbed = false,
                streamRequestDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS,
                streamFrameGapMs = 0L,
                streamJitterMs = 0L,
                streamFrameStale = false,
                streamStaleMs = 0L,
                status = "session closed",
                isError = false,
                permissionState = null,
            )
        }
        stopFramePump()
        stopInputPump()
        stopFrameHealthMonitor()
        resetFrameStreamState()
    }

    fun requestFrameNow() {
        val active = _state.value
        if (active.connected && active.activeSessionId.isNotBlank() && active.activeSessionPeerId.isNotBlank()) {
            client.requestFrame(active.activeSessionPeerId, active.activeSessionId)
            // A manual request is a hint that the user expects a more responsive stream.
            _state.update {
                it.copy(streamRequestDelayMs = max(MIN_STREAM_REQUEST_DELAY_MS, it.streamRequestDelayMs - 20L))
            }
        }
    }

    fun sendText(text: String) {
        val action = JSONObject().apply {
            put("type", "type_text")
            put("text", text)
        }
        sendInputAction(action)
    }

    fun sendMouseMove(x: Int, y: Int) {
        sendInputAction(
            JSONObject().apply {
                put("type", ACTION_MOVE)
                put("x", x)
                put("y", y)
            },
        )
    }

    fun sendMouseButton(button: String, pressed: Boolean) {
        sendInputAction(
            JSONObject().apply {
                put("type", "mouse_button")
                put("button", button)
                put("pressed", pressed)
            },
        )
    }

    fun sendMouseClick(x: Int, y: Int, button: String = "left", count: Int = 1) {
        sendInputAction(
            JSONObject().apply {
                put("type", "click")
                put("button", button)
                put("x", x)
                put("y", y)
                put("count", count)
            },
        )
    }

    fun sendMouseRightClick(x: Int, y: Int) {
        sendMouseClick(x, y, button = "right")
    }

    fun sendDoubleClick(x: Int, y: Int, button: String = "left") {
        sendMouseClick(x, y, button = button, count = 2)
    }

    fun sendScroll(deltaX: Int, deltaY: Int) {
        val clampedX = deltaX.coerceIn(-INPUT_SCROLL_STEP_BOUNDS, INPUT_SCROLL_STEP_BOUNDS)
        val clampedY = deltaY.coerceIn(-INPUT_SCROLL_STEP_BOUNDS, INPUT_SCROLL_STEP_BOUNDS)
        sendInputAction(
            JSONObject().apply {
                put("type", ACTION_SCROLL)
                // Burrow desktop input supports delta_x / delta_y natively.
                put("delta_x", clampedX)
                put("delta_y", clampedY)
            },
        )
    }

    fun sendClipboardAction(action: String) {
        if (action.isBlank()) {
            _state.update { it.copy(status = "invalid clipboard action", isError = true) }
            return
        }

        sendInputAction(
            JSONObject().apply {
                put("type", "clipboard_$action")
            },
        )
    }

    fun sendClipboardText(text: String) {
        val safeText = text.take(2048)
        if (safeText.isBlank()) {
            _state.update { it.copy(status = "clipboard text is empty", isError = true) }
            return
        }

        sendInputAction(
            JSONObject().apply {
                put("type", "clipboard_paste_text")
                put("text", safeText)
            },
        )
    }

    fun sendKey(key: String) {
        sendInputAction(
            JSONObject().apply {
                put("type", "key")
                put("key", key)
                put("modifiers", JSONArray())
            },
        )
    }

    fun sendKeyWithModifiers(key: String, modifiers: List<String>) {
        val modifierArray = JSONArray()
        modifiers.forEach { modifierArray.put(it) }
        sendInputAction(
            JSONObject().apply {
                put("type", "key")
                put("key", key)
                put("modifiers", modifierArray)
            },
        )
    }

    fun sendHotkey(keys: List<String>) {
        val hotkeyArray = JSONArray()
        keys.forEach { hotkeyArray.put(it) }
        sendInputAction(
            JSONObject().apply {
                put("type", "hotkey")
                put("keys", hotkeyArray)
            },
        )
    }

    private fun sendInputAction(action: JSONObject) {
        val active = _state.value
        if (active.activeSessionId.isNotBlank() && active.activeSessionPeerId.isNotBlank()) {
            enqueueInput(active.activeSessionPeerId, active.activeSessionId, action)
            _state.update { it.copy(isError = false) }
        } else {
            _state.update { it.copy(status = "no active session", isError = true) }
        }
    }

    private fun enqueueInput(peerId: String, sessionId: String, action: JSONObject) {
        synchronized(inputSync) {
            when (action.optString("type", "")) {
                ACTION_MOVE -> {
                    // Keep only the latest move event in-flight.
                    pendingMouseMove = action
                }

                ACTION_SCROLL -> {
                    pendingScrollX += action.optInt("delta_x", 0)
                    pendingScrollY += action.optInt("delta_y", 0)
                }

                else -> {
                    if (inputQueue.size >= INPUT_QUEUE_MAX) {
                        // Keep bursts bounded under sustained typing so we don't overrun memory.
                        inputQueue.removeFirst()
                    }
                    inputQueue.addLast(action)
                }
            }
        }
        ensureInputPumpRunning(peerId, sessionId)
    }

    private fun ensureInputPumpRunning(peerId: String, sessionId: String) {
        if (inputPumpRunning) {
            return
        }

        inputPumpRunning = true
        inputPumpJob?.cancel()
        inputPumpJob = viewModelScope.launch {
            while (inputPumpRunning && _state.value.activeSessionId == sessionId && _state.value.activeSessionPeerId == peerId) {
                flushInputBatch(peerId, sessionId)
                delay(INPUT_FLUSH_WINDOW_MS)
            }
            synchronized(inputSync) {
                pendingMouseMove = null
                pendingScrollX = 0
                pendingScrollY = 0
                inputQueue.clear()
            }
            inputPumpRunning = false
        }
    }

    private fun stopInputPump() {
        inputPumpRunning = false
        inputPumpJob?.cancel()
        inputPumpJob = null
        synchronized(inputSync) {
            pendingMouseMove = null
            pendingScrollX = 0
            pendingScrollY = 0
            inputQueue.clear()
        }
    }

    private suspend fun flushInputBatch(peerId: String, sessionId: String) {
        val batch = ArrayList<JSONObject>(INPUT_BATCH_MAX)

        synchronized(inputSync) {
            pendingMouseMove?.let {
                batch.add(it)
                pendingMouseMove = null
            }

            if (pendingScrollX != 0 || pendingScrollY != 0) {
                batch.add(
                    JSONObject().apply {
                        put("type", ACTION_SCROLL)
                        put("delta_x", pendingScrollX)
                        put("delta_y", pendingScrollY)
                    },
                )
                pendingScrollX = 0
                pendingScrollY = 0
            }

            while (batch.size < INPUT_BATCH_MAX && inputQueue.isNotEmpty()) {
                batch.add(inputQueue.removeFirst())
            }
        }

        if (batch.isEmpty()) {
            return
        }

        batch.forEach { action ->
            client.sendInput(peerId, sessionId, action)
        }
    }

    fun sendPrivacy(enabled: Boolean) {
        val active = _state.value
        if (active.activeSessionId.isNotBlank() && active.activeSessionPeerId.isNotBlank()) {
            client.setPrivacy(active.activeSessionPeerId, active.activeSessionId, enabled)
            _state.update { it.copy(status = "privacy=$enabled", isError = false) }
        }
    }

    private fun onSessionReady(session: BurrowDesktopSession) {
        _state.update {
            it.copy(
                activeSession = session,
                activeSessionId = session.sessionId,
                activeSessionPeerId = session.peer ?: it.activeSessionPeerId,
                frameWidth = session.width ?: it.frameWidth,
                frameHeight = session.height ?: it.frameHeight,
                streamFrameStale = false,
                status = "session ready: ${session.state}",
                isError = false,
                streamRequestDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS,
                streamFrameGapMs = 0L,
                streamJitterMs = 0L,
            )
        }

        if (session.sessionId.isNotBlank() && session.peer?.isNotBlank() == true) {
            startFramePump(session.peer, session.sessionId)
            startFrameHealthMonitor(session.peer, session.sessionId)
        }
    }

    private fun onFrame(frame: BurrowDesktopFrame) {
        val active = _state.value
        if (active.activeSessionId.isNotBlank() && frame.sessionId != active.activeSessionId) {
            return
        }

        if (frame.dataBase64.isBlank()) {
            return
        }

        val receivedAt = System.currentTimeMillis()

        viewModelScope.launch(Dispatchers.Default) {
            val bytes = try {
                Base64.decode(frame.dataBase64, Base64.NO_WRAP)
            } catch (_: IllegalArgumentException) {
                withContext(Dispatchers.Main) {
                    _state.update { it.copy(status = "invalid image payload", isError = true) }
                }
                return@launch
            }

            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return@launch

            val gapMs = if (lastFrameWallClockMs > 0L) {
                (receivedAt - lastFrameWallClockMs).coerceAtLeast(1L)
            } else {
                0L
            }
            if (gapMs > 0L) {
                smoothedGapMs = if (smoothedGapMs == 0L) {
                    gapMs
                } else {
                    ((smoothedGapMs * (FRAME_GAP_EWMA_ALPHA_DIVISOR - 1L) + gapMs) / FRAME_GAP_EWMA_ALPHA_DIVISOR)
                }
                streamDelayMs = tuneStreamDelay(gapMs, frame.stubbed)
            }
            lastFrameWallClockMs = receivedAt

            val staleInfo = frameStaleInfo(now = receivedAt, lastFrameAt = receivedAt, smoothedGapMs = smoothedGapMs)

            withContext(Dispatchers.Main) {
                _state.update {
                    it.copy(
                        imageBitmap = bitmap,
                        frameWidth = if (frame.width > 0) frame.width else bitmap.width,
                        frameHeight = if (frame.height > 0) frame.height else bitmap.height,
                        frameSeq = it.frameSeq + 1,
                        lastFrameAt = receivedAt,
                        lastFrameStubbed = frame.stubbed,
                        isError = false,
                        streamRequestDelayMs = streamDelayMs,
                        streamFrameGapMs = smoothedGapMs,
                        streamJitterMs = if (smoothedGapMs > 0L) abs(smoothedGapMs - streamDelayMs) else 0L,
                        streamFrameStale = staleInfo.first,
                        streamStaleMs = staleInfo.second,
                        status = if (frame.stubbed) {
                            "frame received (stubbed)"
                        } else {
                            "streaming"
                        },
                    )
                }
            }
        }
    }

    private fun tuneStreamDelay(gapMs: Long, stubbed: Boolean): Long {
        var next = if (stubbed) {
            streamDelayMs + 20L
        } else {
            streamDelayMs - 8L
        }

        if (gapMs > 0L) {
            if (gapMs > (streamDelayMs * 2L)) {
                next += 40L
            } else if (gapMs < streamDelayMs) {
                next -= 14L
            } else if (gapMs > (streamDelayMs * 11L / 10L)) {
                next += 12L
            }
        }

        return next.coerceIn(MIN_STREAM_REQUEST_DELAY_MS, MAX_STREAM_REQUEST_DELAY_MS)
    }

    private fun staleDelayThreshold(smoothedGapMs: Long): Long {
        val gapFactor = smoothedGapMs.takeIf { it > 0L } ?: FRAME_STALE_WARNING_MS
        return max(FRAME_STALE_WARNING_MS, gapFactor * 4L).coerceAtLeast(800L).coerceAtMost(4000L)
    }

    private fun frameStaleInfo(now: Long, lastFrameAt: Long, smoothedGapMs: Long): Pair<Boolean, Long> {
        val ageMs = now - lastFrameAt
        val thresholdMs = staleDelayThreshold(smoothedGapMs)

        return Pair(ageMs > thresholdMs, ageMs.coerceAtLeast(0L))
    }

    private fun startFramePump(peerId: String, sessionId: String) {
        if (framePumpRunning && framePumpJob?.isActive == true && _state.value.activeSessionId == sessionId) {
            return
        }

        framePumpRunning = true
        streamDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS
        smoothedGapMs = 0L
        lastFrameWallClockMs = 0L
        framePumpJob?.cancel()
        framePumpJob = viewModelScope.launch {
            while (framePumpRunning && _state.value.activeSessionId == sessionId) {
                client.requestFrame(peerId, sessionId)
                val now = System.currentTimeMillis()
                val active = _state.value
                val ageMs = if (active.lastFrameAt > 0L) now - active.lastFrameAt else Long.MAX_VALUE
                val delayMs = if (ageMs > FRAME_STALE_ERROR_MS) {
                    MIN_STREAM_REQUEST_DELAY_MS
                } else if (ageMs > FRAME_STALE_WARNING_MS) {
                    max(MIN_STREAM_REQUEST_DELAY_MS, active.streamRequestDelayMs - 25L)
                } else {
                    active.streamRequestDelayMs
                }
                delay(delayMs)
            }
        }
    }

    private fun stopFramePump() {
        framePumpRunning = false
        framePumpJob?.cancel()
        framePumpJob = null
    }

    private fun startFrameHealthMonitor(peerId: String, sessionId: String) {
        stopFrameHealthMonitor()
        frameHealthJob = viewModelScope.launch {
            while (framePumpRunning && _state.value.activeSessionId == sessionId && _state.value.activeSessionPeerId == peerId) {
                val active = _state.value
                if (active.activeSessionId.isBlank() || active.activeSessionPeerId.isBlank()) {
                    break
                }

                if (active.lastFrameAt > 0L) {
                    val ageMs = System.currentTimeMillis() - active.lastFrameAt
                    val thresholdMs = staleDelayThreshold(active.streamFrameGapMs)
                    val isStale = ageMs > thresholdMs
                    if (isStale != active.streamFrameStale) {
                        _state.update {
                            it.copy(
                                streamFrameStale = isStale,
                                streamStaleMs = ageMs,
                                status = if (isStale) {
                                    "stream stalled (${ageMs}ms)"
                                } else {
                                    it.status
                                },
                            )
                        }
                    } else if (isStale) {
                        _state.update {
                            it.copy(streamStaleMs = ageMs)
                        }
                    }

                    if (ageMs > FRAME_STALE_ERROR_MS) {
                        client.requestFrame(peerId, sessionId)
                    }
                }
                delay(FRAME_HEALTH_POLL_MS)
            }
        }
    }

    private fun stopFrameHealthMonitor() {
        frameHealthJob?.cancel()
        frameHealthJob = null
        _state.update { it.copy(streamFrameStale = false, streamStaleMs = 0L) }
    }

    private fun resetFrameStreamState() {
        streamDelayMs = DEFAULT_STREAM_REQUEST_DELAY_MS
        smoothedGapMs = 0L
        lastFrameWallClockMs = 0L
    }

    override fun onCleared() {
        super.onCleared()
        client.disconnect()
        stopFramePump()
        stopInputPump()
        stopFrameHealthMonitor()
    }
}

data class UiState(
    val serverUri: String = "ws://127.0.0.1:7654",
    val peerName: String = "burrow-android",
    val connected: Boolean = false,
    val peers: List<PeerInfo> = emptyList(),
    val selectedPeerId: String = "",
    val activeSessionId: String = "",
    val activeSession: BurrowDesktopSession? = null,
    val activeSessionPeerId: String = "",
    val status: String = "idle",
    val isError: Boolean = false,
    val imageBitmap: android.graphics.Bitmap? = null,
    val frameWidth: Int = 0,
    val frameHeight: Int = 0,
    val frameSeq: Long = 0,
    val lastFrameAt: Long = 0L,
    val lastFrameStubbed: Boolean = false,
    val streamRequestDelayMs: Long = 120L,
    val streamFrameGapMs: Long = 0L,
    val streamJitterMs: Long = 0L,
    val streamFrameStale: Boolean = false,
    val streamStaleMs: Long = 0L,
    val permissionState: String? = null,
)

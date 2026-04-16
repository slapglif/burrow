package com.nila.burrow.remote.ui

import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.clickable
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.Button
import androidx.compose.material.Checkbox
import androidx.compose.material.CheckboxDefaults
import androidx.compose.material.CircularProgressIndicator
import androidx.compose.material.Divider
import androidx.compose.material.MaterialTheme
import androidx.compose.material.OutlinedTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.RadioButton
import androidx.compose.material.Surface
import androidx.compose.material.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow

import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.nila.burrow.remote.BurrowRemoteViewModel
import com.nila.burrow.remote.model.PeerInfo
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@Composable
fun BurrowRemoteScreen() {
    val viewModel: BurrowRemoteViewModel = viewModel()
    val state by viewModel.state.collectAsState()
    val scrollState = rememberScrollState()
    val context = LocalContext.current
    val clipboardManager = remember(context) {
        context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    }

    val nowMs = System.currentTimeMillis()
    val frameAgeMs = if (state.lastFrameAt > 0L) {
        (nowMs - state.lastFrameAt).coerceAtLeast(0L)
    } else {
        Long.MAX_VALUE
    }
    val effectiveStreamAgeMs = if (state.streamFrameStale && state.streamStaleMs > 0L) {
        state.streamStaleMs
    } else {
        frameAgeMs
    }
    val streamJitterMs = state.streamJitterMs.coerceAtLeast(0L)

    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp)
                .verticalScroll(scrollState),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("Burrow Remote", style = MaterialTheme.typography.h6)

            if (state.status.isNotBlank()) {
                Text(
                    text = state.status,
                    color = if (state.isError) MaterialTheme.colors.error else MaterialTheme.colors.onSurface,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            state.permissionState?.takeIf { it.isNotBlank() }?.let { permission ->
                Text(
                    text = "Permission: $permission",
                    style = MaterialTheme.typography.caption,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            if (!state.connected) {
                OutlinedTextField(
                    value = state.serverUri,
                    onValueChange = viewModel::onServerUriChange,
                    label = { Text("Registry URL") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = state.peerName,
                    onValueChange = viewModel::onPeerNameChange,
                    label = { Text("Peer Name") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = { viewModel.toggleConnection() },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Connect")
                }
            } else {
                Row(horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                    Text("Connected as ${state.peerName}")
                    Button(onClick = { viewModel.toggleConnection() }) {
                        Text("Disconnect")
                    }
                }
            }

            Divider()

            if (state.connected) {
                Text("Peers", style = MaterialTheme.typography.subtitle1)
                if (state.peers.isEmpty()) {
                    Text("No peers yet", style = MaterialTheme.typography.body2)
                }

                state.peers.forEach { peer ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { viewModel.selectPeer(peer.id) }
                            .padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        PeerRow(peer = peer, selected = state.selectedPeerId == peer.id)
                    }
                }

                Spacer(modifier = Modifier.height(8.dp))
                Button(
                    onClick = viewModel::openSelectedSession,
                    enabled = state.selectedPeerId.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Connect to selected peer")
                }
            }

            if (state.activeSessionId.isNotBlank()) {
                val sessionActive = state.activeSessionId.isNotBlank() && state.activeSessionPeerId.isNotBlank()
                Text("Session: ${state.activeSessionId}", style = MaterialTheme.typography.subtitle1)
                Text("Peer: ${state.activeSessionPeerId}", style = MaterialTheme.typography.body2)
                state.activeSession?.let {
                    Text("Backend: ${it.backend ?: "unknown"}", style = MaterialTheme.typography.body2)
                    if (!it.state.isBlank()) {
                        Text("State: ${it.state}", style = MaterialTheme.typography.body2)
                    }
                }

                if (state.frameSeq > 0 || state.streamFrameGapMs > 0L) {
                    val ageMs = if (effectiveStreamAgeMs == Long.MAX_VALUE) "n/a" else "${effectiveStreamAgeMs}ms"
                    val gapMs = if (state.streamFrameGapMs <= 0L) "n/a" else "${state.streamFrameGapMs}ms"
                    val jitterMs = "${streamJitterMs}ms"
                    val freshness = if (state.streamFrameStale) "stale" else "live"
                    Text(
                        "Frames: ${state.frameSeq}  •  Last frame: $ageMs  •  Gap: $gapMs  •  Jitter: $jitterMs  •  ${if (state.lastFrameStubbed) "stubbed" else freshness}",
                        style = MaterialTheme.typography.caption,
                    )
                    Text(
                        "Stream: interval=${state.streamRequestDelayMs}ms  •  status=${if (state.streamFrameStale) "stale" else "stable"}",
                        style = MaterialTheme.typography.caption,
                    )
                }

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = viewModel::requestFrameNow) {
                        Text("Request frame")
                    }
                    Button(onClick = viewModel::closeActiveSession) {
                        Text("Close session")
                    }
                }

                var privacy by remember { mutableStateOf(false) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = privacy,
                        onCheckedChange = {
                            privacy = it
                            viewModel.sendPrivacy(it)
                        },
                        colors = CheckboxDefaults.colors(checkedColor = MaterialTheme.colors.primary),
                    )
                    Text("Blur sensitive areas")
                }

                var chat by remember { mutableStateOf("") }
                val sendCurrentText: () -> Unit = {
                    if (chat.isNotBlank()) {
                        viewModel.sendText(chat)
                        chat = ""
                    }
                }

                OutlinedTextField(
                    value = chat,
                    onValueChange = { chat = it },
                    placeholder = { Text("Type text and send") },
                    modifier = Modifier.fillMaxWidth(),
                    maxLines = 1,
                    keyboardOptions = KeyboardOptions.Default.copy(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { sendCurrentText() }),
                )

                Button(
                    onClick = { sendCurrentText() },
                    modifier = Modifier.align(Alignment.End),
                    enabled = sessionActive,
                ) {
                    Text("Send text")
                }

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { viewModel.sendClipboardAction("copy") }) {
                        Text("Copy")
                    }
                    Button(onClick = { viewModel.sendClipboardAction("cut") }) {
                        Text("Cut")
                    }
                    Button(
                        onClick = {
                            val text = readPrimaryClipboardText(clipboardManager, context)
                            viewModel.sendClipboardText(text)
                        },
                    ) {
                        Text("Paste")
                    }
                    Button(onClick = { viewModel.sendClipboardAction("select_all") }) {
                        Text("Select all")
                    }
                }

                if (sessionActive && state.frameWidth > 0 && state.frameHeight > 0) {
                    val centerX = state.frameWidth / 2
                    val centerY = state.frameHeight / 2
                    Spacer(modifier = Modifier.height(4.dp))

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = { viewModel.sendMouseRightClick(centerX, centerY) },
                        ) {
                            Text("Right click")
                        }
                        Button(
                            onClick = { viewModel.sendDoubleClick(centerX, centerY) },
                        ) {
                            Text("Double click")
                        }
                    }

                    Spacer(modifier = Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { viewModel.sendScroll(0, -4) }) {
                            Text("Scroll up")
                        }
                        Button(onClick = { viewModel.sendScroll(0, 4) }) {
                            Text("Scroll down")
                        }
                        Button(onClick = { viewModel.sendScroll(-4, 0) }) {
                            Text("Scroll left")
                        }
                        Button(onClick = { viewModel.sendScroll(4, 0) }) {
                            Text("Scroll right")
                        }
                    }

                    Spacer(modifier = Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { viewModel.sendKey("tab") }) {
                            Text("Tab")
                        }
                        Button(onClick = { viewModel.sendKey("space") }) {
                            Text("Space")
                        }
                        Button(onClick = { viewModel.sendKey("backspace") }) {
                            Text("Backspace")
                        }
                    }

                    Spacer(modifier = Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { viewModel.sendKey("esc") }) {
                            Text("Esc")
                        }
                        Button(onClick = { viewModel.sendKey("enter") }) {
                            Text("Enter")
                        }
                        Button(onClick = { viewModel.sendKeyWithModifiers("c", listOf("ctrl")) }) {
                            Text("Ctrl+C")
                        }
                        Button(onClick = { viewModel.sendKeyWithModifiers("v", listOf("ctrl")) }) {
                            Text("Ctrl+V")
                        }
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { viewModel.sendKey("up") }) {
                            Text("Up")
                        }
                        Button(onClick = { viewModel.sendKey("left") }) {
                            Text("Left")
                        }
                        Button(onClick = { viewModel.sendKey("down") }) {
                            Text("Down")
                        }
                        Button(onClick = { viewModel.sendKey("right") }) {
                            Text("Right")
                        }
                    }
                }
            }

                val remoteAspect = if (state.frameWidth > 0 && state.frameHeight > 0) {
                    state.frameWidth.toFloat() / state.frameHeight.toFloat()
                } else {
                    16f / 9f
                }
                val frameBitmap = state.imageBitmap
                if (frameBitmap != null) {
                var displaySize by remember { mutableStateOf(IntSize.Zero) }
                Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    RemoteBitmapImage(
                        bitmap = frameBitmap,
                        modifier = Modifier
                            .fillMaxWidth()
                            .aspectRatio(remoteAspect)
                            .onGloballyPositioned { layoutCoordinates ->
                                displaySize = layoutCoordinates.size
                            }
                            .pointerInput(state.activeSessionId, state.frameWidth, state.frameHeight, displaySize) {
                                if (state.activeSessionId.isBlank() || displaySize.width == 0 || displaySize.height == 0) {
                                    return@pointerInput
                                }

                                val frameWidth = state.frameWidth
                                val frameHeight = state.frameHeight

                                suspend fun toRemote(offset: Offset): Pair<Int, Int>? {
                                    if (frameWidth <= 0 || frameHeight <= 0 || displaySize.width == 0 || displaySize.height == 0) {
                                        return null
                                    }
                                    val remoteX = ((offset.x / displaySize.width.toFloat()) * frameWidth)
                                        .roundToInt()
                                        .coerceIn(0, maxOf(0, frameWidth - 1))
                                    val remoteY = ((offset.y / displaySize.height.toFloat()) * frameHeight)
                                        .roundToInt()
                                        .coerceIn(0, maxOf(0, frameHeight - 1))
                                    return remoteX to remoteY
                                }

                                coroutineScope {
                                    launch {
                                        detectTapGestures(
                                            onTap = { tap ->
                                                launch {
                                                    toRemote(tap)?.let { (x, y) ->
                                                        viewModel.sendMouseClick(x, y)
                                                    }
                                                }
                                            },
                                            onDoubleTap = { tap ->
                                                launch {
                                                    toRemote(tap)?.let { (x, y) ->
                                                        viewModel.sendDoubleClick(x, y)
                                                    }
                                                }
                                            },
                                            onLongPress = { tap ->
                                                launch {
                                                    toRemote(tap)?.let { (x, y) ->
                                                        viewModel.sendMouseRightClick(x, y)
                                                    }
                                                }
                                            },
                                        )
                                    }
                                    launch {
                                        detectDragGestures(
                                            onDragStart = { start ->
                                                launch {
                                                    toRemote(start)?.let { (x, y) ->
                                                        viewModel.sendMouseMove(x, y)
                                                        viewModel.sendMouseButton("left", true)
                                                    }
                                                }
                                            },
                                            onDrag = { change, _ ->
                                                launch {
                                                    toRemote(change.position)?.let { (x, y) ->
                                                        viewModel.sendMouseMove(x, y)
                                                    }
                                                }
                                                change.consume()
                                            },
                                            onDragEnd = {
                                                launch { viewModel.sendMouseButton("left", false) }
                                            },
                                            onDragCancel = {
                                                launch { viewModel.sendMouseButton("left", false) }
                                            },
                                        )
                                    }
                                }
                            },
                    )
                }
            } else if (state.activeSessionId.isNotBlank()) {
                CircularProgressIndicator()
            }
        }
    }
}

@Composable
private fun PeerRow(peer: PeerInfo, selected: Boolean) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(peer.name, style = MaterialTheme.typography.body1)
                Text(peer.id, fontSize = 11.sp, color = MaterialTheme.colors.onSurface.copy(alpha = 0.7f))
            }
            RadioButton(selected = selected, onClick = {})
        }
    }
}

private fun readPrimaryClipboardText(clipboardManager: ClipboardManager, context: Context): String {
    val clip = clipboardManager.primaryClip
    if (clip == null || clip.itemCount == 0) {
        return ""
    }

    val item = clip.getItemAt(0)
    return item?.coerceToText(context).toString()
}

@Composable
private fun RemoteBitmapImage(bitmap: Bitmap, modifier: Modifier = Modifier) {
    Image(
        bitmap = bitmap.asImageBitmap(),
        contentDescription = "Remote desktop frame",
        modifier = modifier,
    )
}

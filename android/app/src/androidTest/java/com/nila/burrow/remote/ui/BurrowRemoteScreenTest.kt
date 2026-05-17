package com.nila.burrow.remote.ui

import androidx.activity.ComponentActivity
import androidx.compose.material.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.nila.burrow.remote.UiState
import com.nila.burrow.remote.model.BurrowDesktopSession
import com.nila.burrow.remote.model.PeerInfo
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BurrowRemoteScreenTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun disconnected_screen_shows_connect_form() {
        composeRule.setContent {
            MaterialTheme {
                BurrowRemoteScreenContent(
                    state = UiState(status = ""),
                    actions = BurrowRemoteActions(),
                )
            }
        }

        composeRule.onNodeWithText("Burrow Remote").assertIsDisplayed()
        composeRule.onNodeWithText("Registry URL").assertIsDisplayed()
        composeRule.onNodeWithText("Peer Name").assertIsDisplayed()
        composeRule.onNodeWithText("Connect").assertIsDisplayed()
    }

    @Test
    fun connected_screen_shows_peer_section() {
        composeRule.setContent {
            MaterialTheme {
                BurrowRemoteScreenContent(
                    state = UiState(
                        connected = true,
                        peerName = "android-peer",
                        status = ""
                    ),
                    actions = BurrowRemoteActions(),
                )
            }
        }

        composeRule.onNodeWithText("Connected as android-peer").assertIsDisplayed()
        composeRule.onNodeWithText("Peers").assertIsDisplayed()
    }

    @Test
    fun selecting_peer_enables_connect_to_selected_peer() {
        val peers = listOf(
            PeerInfo(id = "peer-1", name = "Peer One"),
            PeerInfo(id = "peer-2", name = "Peer Two"),
        )
        var state by mutableStateOf(
            UiState(
                connected = true,
                peers = peers,
                peerName = "android-peer",
                status = ""
            )
        )

        composeRule.setContent {
            MaterialTheme {
                BurrowRemoteScreenContent(
                    state = state,
                    actions = BurrowRemoteActions(
                        onSelectPeer = { selectedPeerId ->
                            state = state.copy(selectedPeerId = selectedPeerId)
                        },
                    ),
                )
            }
        }

        composeRule.onNodeWithText("Connect to selected peer").assertIsNotEnabled()
        composeRule.onNodeWithText("Peer Two").performClick()
        composeRule.onNodeWithText("Connect to selected peer").assertIsEnabled()
        composeRule.runOnIdle {
            check(state.selectedPeerId == "peer-2")
        }
    }

    @Test
    fun active_session_renders_controls_and_can_close_session() {
        var state by mutableStateOf(
            UiState(
                connected = true,
                peerName = "android-peer",
                activeSessionId = "session-123",
                activeSessionPeerId = "peer-2",
                activeSession = BurrowDesktopSession(
                    sessionId = "session-123",
                    peer = "peer-2",
                    backend = "desktop",
                    state = "ready",
                ),
                status = ""
            )
        )

        composeRule.setContent {
            MaterialTheme {
                BurrowRemoteScreenContent(
                    state = state,
                    actions = BurrowRemoteActions(
                        onCloseActiveSession = {
                            state = state.copy(
                                activeSessionId = "",
                                activeSessionPeerId = "",
                                activeSession = null,
                            )
                        },
                    ),
                )
            }
        }

        composeRule.onNodeWithText("Session: session-123").assertIsDisplayed()
        composeRule.onNodeWithText("Peer: peer-2").assertIsDisplayed()
        composeRule.onNodeWithText("Backend: desktop").assertIsDisplayed()
        composeRule.onNodeWithText("State: ready").assertIsDisplayed()
        composeRule.onNodeWithText("Request frame").assertIsDisplayed()
        composeRule.onNodeWithText("Close session").assertIsDisplayed()
        composeRule.onNodeWithText("Blur sensitive areas").assertIsDisplayed()
        composeRule.onNodeWithText("Send text").assertIsDisplayed()
        composeRule.onNodeWithText("Copy").performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText("Cut").assertIsDisplayed()
        composeRule.onNodeWithText("Paste").assertIsDisplayed()
        composeRule.onNodeWithText("Select all").assertIsDisplayed()

        composeRule.onNodeWithText("Close session").performClick()
        composeRule.onAllNodesWithText("Session: session-123").assertCountEquals(0)
        composeRule.onAllNodesWithText("Peer: peer-2").assertCountEquals(0)
        composeRule.onAllNodesWithText("Backend: desktop").assertCountEquals(0)
        composeRule.onAllNodesWithText("Close session").assertCountEquals(0)
    }

    @Test
    fun send_text_uses_callback() {
        var sentText = ""

        composeRule.setContent {
            MaterialTheme {
                BurrowRemoteScreenContent(
                    state = UiState(
                        connected = true,
                        peerName = "android-peer",
                        activeSessionId = "session-123",
                        activeSessionPeerId = "peer-2",
                        status = ""
                    ),
                    actions = BurrowRemoteActions(
                        onSendText = { sentText = it },
                    ),
                )
            }
        }

        composeRule.onNodeWithTag("send-text-field").performTextInput("hello burrow")
        composeRule.onNodeWithText("Send text").performClick()

        composeRule.runOnIdle {
            check(sentText == "hello burrow")
        }
    }
}

package com.nila.burrow.remote

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material.MaterialTheme
import androidx.compose.material.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.nila.burrow.remote.ui.BurrowRemoteScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableEdgeToEdge()
        setContent {
            BurrowRemoteApp()
        }
    }
}

@Composable
private fun BurrowRemoteApp() {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            BurrowRemoteScreen()
        }
    }
}

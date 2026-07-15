package com.omnipad.client

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.MouseClick
import com.omnipad.client.network.MouseMove
import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.OmniPadConnection
import com.omnipad.client.network.Scroll
import com.omnipad.client.network.TextInput
import com.omnipad.client.ui.screens.ConnectScreen
import com.omnipad.client.ui.screens.ControlScreen
import com.omnipad.client.ui.theme.OmniPadTheme

class MainActivity : ComponentActivity() {

    private val connection = OmniPadConnection(lifecycleScope)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            OmniPadTheme {
                val state by connection.connectionState.collectAsState()

                if (state == ConnectionState.CONNECTED) {
                    ControlScreen(
                        onDisconnect = { connection.disconnect() },
                        onMouseMove = { dx, dy ->
                            connection.sendMessage(MouseMove(dx, dy))
                        },
                        onLeftClick = {
                            connection.sendMessage(MouseClick("left", "click"))
                        },
                        onRightClick = {
                            connection.sendMessage(MouseClick("right", "click"))
                        },
                        onScroll = { delta ->
                            connection.sendMessage(Scroll(delta))
                        },
                        onTextInput = { text ->
                            connection.sendMessage(TextInput(text))
                        },
                        onKeyboard = { key, action ->
                            connection.sendMessage(Keyboard(key, action))
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    ConnectScreen(
                        connectionState = state,
                        onConnect = { host, port ->
                            connection.connect(host, port)
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }
    }

    override fun onDestroy() {
        connection.disconnect()
        super.onDestroy()
    }
}

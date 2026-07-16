package com.omnipad.client

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.OmniPadConnection
import com.omnipad.client.network.RecentHostsStore
import com.omnipad.client.ui.screens.ConnectScreen
import com.omnipad.client.ui.screens.TouchpadScreen
import com.omnipad.client.ui.theme.OmniPadTheme

class MainActivity : ComponentActivity() {

    private val connection = OmniPadConnection(lifecycleScope)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val hostsStore = RecentHostsStore(this)

        setContent {
            OmniPadTheme {
                val state by connection.connectionState.collectAsState()
                var recentHosts by remember { mutableStateOf(hostsStore.get()) }

                if (state == ConnectionState.CONNECTED) {
                    TouchpadScreen(
                        onDisconnect = { connection.disconnect() },
                        onSendMessage = { connection.sendMessage(it) },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    ConnectScreen(
                        connectionState = state,
                        recentHosts = recentHosts,
                        onConnect = { host, port ->
                            connection.connect(host, port) {
                                hostsStore.add(host, port)
                                recentHosts = hostsStore.get()
                            }
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

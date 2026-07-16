package com.omnipad.client

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.Error
import com.omnipad.client.network.HeartbeatAck
import com.omnipad.client.network.OmniPadConnection
import com.omnipad.client.network.RecentHostsStore
import com.omnipad.client.ui.screens.ConnectScreen
import com.omnipad.client.ui.screens.TouchpadScreen
import com.omnipad.client.ui.theme.OmniPadTheme
import kotlinx.coroutines.delay

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
                var missedHeartbeats by remember { mutableIntStateOf(0) }
                var autoDisconnect by remember { mutableStateOf(true) }

                connection.setOnMessageListener { msg ->
                    when (msg) {
                        is HeartbeatAck -> missedHeartbeats = 0
                        is Error -> {
                            val text = "服务器错误: ${msg.message}"
                            Toast.makeText(this@MainActivity, text, Toast.LENGTH_SHORT).show()
                        }
                        else -> {}
                    }
                }

                LaunchedEffect(state, autoDisconnect) {
                    if (state == ConnectionState.CONNECTED && autoDisconnect) {
                        while (true) {
                            delay(5000)
                            missedHeartbeats++
                            if (missedHeartbeats >= 3) {
                                connection.disconnect()
                                Toast.makeText(
                                    this@MainActivity, "连接已断开：服务器无响应",
                                    Toast.LENGTH_LONG,
                                ).show()
                                missedHeartbeats = 0
                                break
                            }
                        }
                    }
                }

                if (state == ConnectionState.CONNECTED) {
                    TouchpadScreen(
                        onDisconnect = {
                            missedHeartbeats = 0
                            connection.disconnect()
                        },
                        onSendMessage = { connection.sendMessage(it) },
                        autoDisconnect = autoDisconnect,
                        onToggleAutoDisconnect = { autoDisconnect = !autoDisconnect },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    ConnectScreen(
                        connectionState = state,
                        recentHosts = recentHosts,
                        onConnect = { host, port ->
                            hostsStore.add(host, port)
                            recentHosts = hostsStore.get()
                            connection.connect(host, port)
                        },
                        onDeleteHost = { host, port ->
                            hostsStore.remove(host, port)
                            recentHosts = hostsStore.get()
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

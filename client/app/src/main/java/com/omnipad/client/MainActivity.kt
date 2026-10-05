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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.Error
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
                val lastError by connection.lastError.collectAsState()
                var recentHosts by remember { mutableStateOf(hostsStore.get()) }
                var autoDisconnect by remember { mutableStateOf(true) }

                // 监听器只在进入组合时设置一次。原先直接写在组合体内，
                // 每次重组都会重新赋值，是典型的副作用误用。
                LaunchedEffect(Unit) {
                    connection.setOnMessageListener { msg ->
                        if (msg is Error) {
                            Toast.makeText(
                                this@MainActivity,
                                "服务器错误: ${msg.message}",
                                Toast.LENGTH_SHORT,
                            ).show()
                        }
                    }
                }

                // 心跳与超时判定都在连接层，这里只把用户开关同步过去
                LaunchedEffect(autoDisconnect) {
                    connection.autoDisconnect = autoDisconnect
                }

                LaunchedEffect(lastError) {
                    val message = lastError ?: return@LaunchedEffect
                    Toast.makeText(this@MainActivity, message, Toast.LENGTH_LONG).show()
                    connection.clearLastError()
                }

                if (state == ConnectionState.CONNECTED) {
                    TouchpadScreen(
                        onDisconnect = { connection.disconnect() },
                        onSendMessage = { connection.sendMessage(it) },
                        autoDisconnect = autoDisconnect,
                        onToggleAutoDisconnect = { autoDisconnect = !autoDisconnect },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    ConnectScreen(
                        connectionState = state,
                        recentHosts = recentHosts,
                        onConnect = { host, port, token ->
                            hostsStore.add(host, port, token)
                            recentHosts = hostsStore.get()
                            connection.connect(host, port, token)
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

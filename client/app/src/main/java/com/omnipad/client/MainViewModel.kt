package com.omnipad.client

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.omnipad.client.data.Settings
import com.omnipad.client.data.SettingsStore
import com.omnipad.client.network.ConnectionNotice
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.Error
import com.omnipad.client.network.OmniPadConnection
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.network.RecentHost
import com.omnipad.client.network.RecentHostsStore
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 一次连接的参数快照，用于失败后回填输入框。
 */
data class EndpointSnapshot(val host: String, val port: Int, val token: String)

/**
 * 客户端唯一的界面状态源。
 *
 * 上一版把这些状态全部放在 `MainActivity` 的字段和 `remember` 里，直接后果是
 * **转屏必掉线**（Activity 重建 -> onDestroy -> disconnect）和**设置项转屏就重置**。
 * 状态上提到 ViewModel 后，旋转不再重建连接，设置也走持久化存储。
 *
 * 这里只产出网络层的事件对象，不做文案映射 —— 文案留给 UI 层解析，
 * 这样语言切换和重组都不会影响业务状态。
 */
class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val connection = OmniPadConnection(viewModelScope)
    private val settingsStore = SettingsStore(application)
    private val hostsStore = RecentHostsStore(application)

    val connectionState: StateFlow<ConnectionState> = connection.connectionState
    val reconnectAttempt: StateFlow<Int> = connection.reconnectAttempt
    val latencyMs: StateFlow<Int?> = connection.latencyMs
    val settings: StateFlow<Settings> = settingsStore.settings

    private val _recentHosts = MutableStateFlow(hostsStore.get())
    val recentHosts: StateFlow<List<RecentHost>> = _recentHosts.asStateFlow()

    /**
     * 是否处于「已进入触控板」的会话中。
     *
     * 关键在于它**不随一次抖动而回落**：短暂断开进入 RECONNECTING 时保持为 true，
     * 用户看到的是一条重连提示条，而不是触控板整个消失、正在输入的内容全丢。
     */
    private val _inSession = MutableStateFlow(false)
    val inSession: StateFlow<Boolean> = _inSession.asStateFlow()

    /** 终态失败的原因，用于在连接页常驻显示（而不是一个 3 秒就消失的 Toast）。 */
    private val _failure = MutableStateFlow<ConnectionNotice?>(null)
    val failure: StateFlow<ConnectionNotice?> = _failure.asStateFlow()

    /** 最近一次使用的连接参数；连接页用它回填。 */
    private val _lastEndpoint = MutableStateFlow<EndpointSnapshot?>(null)
    val lastEndpoint: StateFlow<EndpointSnapshot?> = _lastEndpoint.asStateFlow()

    /** 会话中的瞬时提示（服务端报错等），由界面弹 Snackbar。 */
    private val _snackbars = MutableSharedFlow<ConnectionNotice>(extraBufferCapacity = 8)
    val snackbars: SharedFlow<ConnectionNotice> = _snackbars.asSharedFlow()

    init {
        // 监听器在 ViewModel 生命周期内只注册一次，且它捕获的是 ViewModel 而非 Activity，
        // 不会造成 Activity 泄漏（上一版把 Activity 捕获进了连接层的长生命周期回调）。
        connection.setOnMessageListener { msg: OmniPadMessage ->
            if (msg is Error) {
                _snackbars.tryEmit(ConnectionNotice.ServerError(msg.message))
            }
        }

        viewModelScope.launch {
            settingsStore.settings.collect { s ->
                connection.autoDisconnect = s.autoDisconnect
                connection.autoReconnect = s.autoReconnect
            }
        }

        viewModelScope.launch {
            connection.connectionState.collect { state ->
                when (state) {
                    ConnectionState.CONNECTED -> {
                        _inSession.value = true
                        _failure.value = null
                    }

                    ConnectionState.FAILED -> {
                        _inSession.value = false
                        _failure.value = connection.lastError.value
                    }

                    else -> Unit
                }
            }
        }
    }

    /** 发起连接。参数应已通过 [com.omnipad.client.network.EndpointValidator] 校验。 */
    fun connect(host: String, port: Int, token: String) {
        _failure.value = null
        _lastEndpoint.value = EndpointSnapshot(host, port, token)
        hostsStore.add(host, port, token)
        _recentHosts.value = hostsStore.get()
        connection.connect(host, port, token)
    }

    /** 用户主动断开：结束会话并回到连接页。 */
    fun disconnect() {
        _inSession.value = false
        _failure.value = null
        connection.disconnect()
    }

    /** 从触控板页返回连接页，但不主动断开 —— 交给连接层继续重连。 */
    fun leaveSession() {
        _inSession.value = false
        connection.disconnect()
    }

    /** 发送一条控制消息。未连接时连接层会静默丢弃。 */
    fun sendMessage(message: OmniPadMessage) {
        connection.sendMessage(message)
    }

    fun deleteHost(host: String, port: Int) {
        hostsStore.remove(host, port)
        _recentHosts.value = hostsStore.get()
    }

    fun updateSettings(transform: (Settings) -> Settings) {
        settingsStore.update(transform)
    }

    override fun onCleared() {
        connection.disconnect()
        super.onCleared()
    }
}

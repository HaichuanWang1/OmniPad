package com.omnipad.client.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.omnipad.client.network.ConnectionState

@Composable
fun ConnectScreen(
    connectionState: ConnectionState,
    onConnect: (host: String, port: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    var host by remember { mutableStateOf("192.168.") }
    var port by remember { mutableStateOf("5800") }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "OmniPad",
            style = MaterialTheme.typography.displayLarge,
            color = MaterialTheme.colorScheme.primary,
        )

        Spacer(Modifier.height(8.dp))

        Text(
            text = "远程控制你的电脑",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(48.dp))

        OutlinedTextField(
            value = host,
            onValueChange = { host = it },
            label = { Text("服务器地址") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )

        Spacer(Modifier.height(12.dp))

        OutlinedTextField(
            value = port,
            onValueChange = { port = it.filter { c -> c.isDigit() } },
            label = { Text("端口") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )

        Spacer(Modifier.height(24.dp))

        Button(
            onClick = { onConnect(host, port.toIntOrNull() ?: 5800) },
            enabled = connectionState != ConnectionState.CONNECTING,
            modifier = Modifier.fillMaxWidth().height(52.dp),
        ) {
            if (connectionState == ConnectionState.CONNECTING) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Text("连接", style = MaterialTheme.typography.titleMedium)
            }
        }

        if (connectionState == ConnectionState.FAILED) {
            Spacer(Modifier.height(16.dp))
            Text(
                text = "连接失败，请检查地址和端口",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

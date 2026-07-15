package com.omnipad.client.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.omnipad.client.ui.components.Touchpad

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ControlScreen(
    onDisconnect: () -> Unit,
    onMouseMove: (dx: Int, dy: Int) -> Unit,
    onLeftClick: () -> Unit,
    onRightClick: () -> Unit,
    onScroll: (delta: Int) -> Unit,
    onTextInput: (text: String) -> Unit,
    onKeyboard: (key: String, action: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var textFieldValue by remember { mutableStateOf("") }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("OmniPad") },
                navigationIcon = {
                    IconButton(onClick = onDisconnect) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "断开")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                )
            )
        }
    ) { padding ->
        Column(
            modifier = modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Touchpad(
                onDrag = { dx, dy ->
                    if (dx in -500f..500f && dy in -500f..500f) {
                        onMouseMove(dx.toInt(), dy.toInt())
                    }
                },
                onTap = onLeftClick,
                onSecondaryTap = onRightClick,
            )

            Spacer(Modifier.height(4.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                FilledIconButton(
                    onClick = { onScroll(1) },
                    modifier = Modifier.size(56.dp),
                    shape = CircleShape,
                ) {
                    Text("△", color = MaterialTheme.colorScheme.onPrimary)
                }

                FilledIconButton(
                    onClick = { onScroll(-1) },
                    modifier = Modifier.size(56.dp),
                    shape = CircleShape,
                ) {
                    Text("▽", color = MaterialTheme.colorScheme.onPrimary)
                }

                Spacer(Modifier.width(8.dp))

                FilledIconButton(
                    onClick = onLeftClick,
                    modifier = Modifier.size(56.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("左键", color = MaterialTheme.colorScheme.onPrimary)
                }

                FilledIconButton(
                    onClick = onRightClick,
                    modifier = Modifier.size(56.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("右键", color = MaterialTheme.colorScheme.onPrimary)
                }
            }

            Spacer(Modifier.height(4.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = textFieldValue,
                    onValueChange = { textFieldValue = it },
                    label = { Text("输入文字") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp),
                )
                FilledIconButton(
                    onClick = {
                        if (textFieldValue.isNotEmpty()) {
                            onTextInput(textFieldValue)
                            textFieldValue = ""
                        }
                    },
                    modifier = Modifier.size(56.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("发送", color = MaterialTheme.colorScheme.onPrimary)
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf("Enter", "Tab", "Esc", "Back").forEach { key ->
                    val k = when (key) {
                        "Back" -> "backspace"
                        "Esc" -> "escape"
                        else -> key.lowercase()
                    }
                    FilledIconButton(
                        onClick = { onKeyboard(k, "press") },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = RoundedCornerShape(10.dp),
                    ) {
                        Text(key, color = MaterialTheme.colorScheme.onPrimary)
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf("↑", "↓", "←", "→").forEach { key ->
                    val k = when (key) {
                        "↑" -> "up"; "↓" -> "down"; "←" -> "left"; "→" -> "right"
                        else -> key
                    }
                    FilledIconButton(
                        onClick = { onKeyboard(k, "press") },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = RoundedCornerShape(10.dp),
                    ) {
                        Text(key, color = MaterialTheme.colorScheme.onPrimary)
                    }
                }
            }
        }
    }
}

import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// 签名身份从 client/keystore.properties 读取，该文件不入库。
// 密钥与口令绝不能写进 build.gradle.kts —— 那等于把签名身份提交进仓库。
// 缺少该文件时仍可构建 debug，release 则产出未签名包。
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        keystorePropertiesFile.inputStream().use { load(it) }
    }
}
val hasSigningConfig = keystoreProperties.getProperty("storeFile") != null

// 版本号唯一来源是仓库根目录的 VERSION，scripts/package.ps1 也读同一个文件。
// 此前 versionName、APK 文件名、zip 文件名各写一遍，已经漂移过一次
// （发布资产里出现了 Gradle 原始输出名 app-release.apk）。
val versionFile = rootProject.projectDir.parentFile.resolve("VERSION")
val appVersionName = versionFile.takeIf { it.isFile }
    ?.readText()?.trim()
    ?.takeIf { it.isNotEmpty() }
    ?: error("读不到版本号：$versionFile")

// versionCode 必须单调递增，规则：
//   1.0.0-beta1.6 -> 6       沿用既有 beta 发布习惯（beta1.5 的 versionCode 就是 5）
//   1.0.0         -> 10000   正式版改用 major*10000 + minor*100 + patch
// 这样 beta 转正式版时数值自然抬升，不会出现新版本装不上去的情况。
val appVersionCode = Regex("""-beta\d+\.(\d+)$""").find(appVersionName)
    ?.groupValues?.get(1)?.toInt()
    ?: appVersionName.split('.').let { parts ->
        val (major, minor, patch) = List(3) { parts.getOrNull(it)?.toIntOrNull() ?: 0 }
        major * 10000 + minor * 100 + patch
    }

android {
    namespace = "com.omnipad.client"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.omnipad.client"
        minSdk = 26
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersionName
    }

    signingConfigs {
        if (hasSigningConfig) {
            create("release") {
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // 客户端没有任何反射查找，Compose 与协程自带 consumer 规则，
            // 因此不需要额外的 keep 规则；proguard-rules.pro 只补了崩溃堆栈可读性。
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (hasSigningConfig) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    // 这里曾经是 lint { checkReleaseBuilds = false }：AGP 8.2.0 的 lint 无法解析
    // SDK 中形如 android-37.0 的平台目录名，会在 lintVitalAnalyzeRelease 抛
    // NumberFormatException，连带 assembleRelease 整体失败（fix.md 第 22 条）。
    // 升级到 AGP 8.13.2 后已根治，门禁恢复默认开启。

    buildFeatures {
        compose = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.7"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2023.10.01")
    implementation(composeBom)

    implementation("androidx.compose.material3:material3")
    // 触控板页要用到 Mouse / Keyboard / ContentPaste / PowerSettingsNew 等图标，
    // 这些不在 material-icons-core 里。R8 只会保留真正引用到的图标，release 包
    // 体积基本不受影响（见 CHANGELOG 里的体积对比）。
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    // viewModel() 组合函数 + viewModelScope。连接状态放在 ViewModel 里跨旋转存活，
    // 这是修掉「转屏掉线」的关键依赖。
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // 扫码配对：CameraX 负责取景与取帧，ZXing 负责解二维码。
    //
    // 为什么是 ZXing 而不是 ML Kit：ZXing core 是纯 Java、离线、不依赖 Google 服务，
    // 打包进去约 0.5 MB（ML Kit 的 bundled 条码包约 3 MB）。更关键的是它能在普通
    // JVM 单元测试里跑 —— 服务端那个自研的纯 Python 二维码编码器就是靠它验证的：
    // `PairingQrTest` 直接解码服务端生成的 `pairing-qr-v1.png`。
    val cameraX = "1.3.4"
    implementation("androidx.camera:camera-core:$cameraX")
    implementation("androidx.camera:camera-camera2:$cameraX")
    implementation("androidx.camera:camera-lifecycle:$cameraX")
    implementation("androidx.camera:camera-view:$cameraX")
    implementation("com.google.zxing:core:3.5.3")

    testImplementation("junit:junit:4.13.2")
    // 单元测试跑在普通 JVM 上，android.jar 里的 org.json 是空壳（调用即抛
    // "not mocked"），所以补一份参考实现。它排在 mockable-android.jar 之前，
    // 因此测试里拿到的是真实可用的 JSONObject。
    testImplementation("org.json:json:20231013")
}

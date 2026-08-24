package com.ember.companion

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import com.ember.companion.live2d.EmberLive2DPlatformView
import com.ember.companion.live2d.EmberLive2DViewFactory
import java.util.concurrent.Executors

class MainActivity : FlutterActivity() {
    private val pythonExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        flutterEngine.platformViewsController.registry.registerViewFactory(
            LIVE2D_VIEW_TYPE,
            EmberLive2DViewFactory(),
        )

        val coreChannel = MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL_NAME,
        )
        EmberRuntimeEvents.listener = { eventJson ->
            mainHandler.post { coreChannel.invokeMethod("llmEvent", eventJson) }
        }
        coreChannel.setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "pythonInfo" -> result.success(PythonRuntime.pythonInfo(this))
                    "echo" -> {
                        val value = call.argument<String>("value").orEmpty()
                        result.success(PythonRuntime.echo(this, value))
                    }
                    "startEngine" -> {
                        requestNotificationPermissionIfNeeded()
                        val intent = Intent(this, EmberForegroundService::class.java)
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            startForegroundService(intent)
                        } else {
                            startService(intent)
                        }
                        result.success(true)
                    }
                    "stopEngine" -> {
                        result.success(
                            stopService(Intent(this, EmberForegroundService::class.java)),
                        )
                    }
                    "engineStatus" -> result.success(PythonRuntime.status(this))
                    "getMemoryOverview" -> {
                        pythonExecutor.execute {
                            try {
                                val response = PythonRuntime.getMemoryOverview(this)
                                mainHandler.post { result.success(response) }
                            } catch (error: Exception) {
                                mainHandler.post {
                                    result.error("EMBER_MEMORY_ERROR", error.message, null)
                                }
                            }
                        }
                    }
                    "recordInteraction" -> result.success(
                        PythonRuntime.recordInteraction(this),
                    )
                    "setPad" -> result.success(
                        PythonRuntime.setPad(
                            this,
                            call.argument<Double>("pleasure") ?: 5.0,
                            call.argument<Double>("arousal") ?: 5.0,
                            call.argument<Double>("dominance") ?: 5.0,
                        ),
                    )
                    "getLlmConfig" -> result.success(PythonRuntime.getLlmConfig(this))
                    "updateAppConfig" -> {
                        val configJson = call.argument<String>("configJson").orEmpty()
                        result.success(PythonRuntime.updateAppConfig(this, configJson))
                    }
                    "updateLlmConfig" -> result.success(
                        PythonRuntime.updateLlmConfig(
                            this,
                            call.argument<String>("baseUrl").orEmpty(),
                            call.argument<String>("model").orEmpty(),
                            call.argument<String>("smallModel").orEmpty(),
                            call.argument<String>("apiKey"),
                            call.argument<Double>("temperature") ?: 0.7,
                            call.argument<Boolean>("stateUpdatesEnabled") ?: true,
                        ),
                    )
                    "getChatHistory" -> result.success(PythonRuntime.getChatHistory(this))
                    "clearChatHistory" -> result.success(PythonRuntime.clearChatHistory(this))
                    "listArchives" -> result.success(PythonRuntime.listArchives(this))
                    "createArchive" -> {
                        val name = call.argument<String>("name").orEmpty()
                        pythonExecutor.execute {
                            try {
                                val response = PythonRuntime.createArchive(this, name)
                                mainHandler.post { result.success(response) }
                            } catch (error: Exception) {
                                mainHandler.post {
                                    result.error("EMBER_ARCHIVE_ERROR", error.message, null)
                                }
                            }
                        }
                    }
                    "loadArchive" -> {
                        val archiveId = call.argument<String>("id").orEmpty()
                        pythonExecutor.execute {
                            try {
                                val response = PythonRuntime.loadArchive(this, archiveId)
                                mainHandler.post { result.success(response) }
                            } catch (error: Exception) {
                                mainHandler.post {
                                    result.error("EMBER_ARCHIVE_ERROR", error.message, null)
                                }
                            }
                        }
                    }
                    "deleteArchive" -> {
                        val archiveId = call.argument<String>("id").orEmpty()
                        result.success(PythonRuntime.deleteArchive(this, archiveId))
                    }
                    "sendMessage" -> {
                        val text = call.argument<String>("text").orEmpty()
                        pythonExecutor.execute {
                            try {
                                val response = PythonRuntime.sendMessage(this, text)
                                mainHandler.post { result.success(response) }
                            } catch (error: Exception) {
                                mainHandler.post {
                                    result.error("EMBER_CHAT_ERROR", error.message, null)
                                }
                            }
                        }
                    }
                    "sendMessageStream" -> {
                        val text = call.argument<String>("text").orEmpty()
                        val imageSize = call.argument<String>("imageSize")
                        pythonExecutor.execute {
                            try {
                                val response = PythonRuntime.sendMessageStream(
                                    this,
                                    text,
                                    imageSize,
                                    PythonStreamCallback { eventJson ->
                                        mainHandler.post {
                                            coreChannel.invokeMethod("llmEvent", eventJson)
                                        }
                                    },
                                )
                                mainHandler.post { result.success(response) }
                            } catch (error: Exception) {
                                mainHandler.post {
                                    result.error("EMBER_CHAT_ERROR", error.message, null)
                                }
                            }
                        }
                    }
                    else -> result.notImplemented()
                }
            } catch (error: Exception) {
                result.error("EMBER_NATIVE_ERROR", error.message, null)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        EmberRuntimeEvents.appInForeground = true
        EmberLive2DPlatformView.resumeCurrent()
    }

    override fun onPause() {
        EmberRuntimeEvents.appInForeground = false
        EmberLive2DPlatformView.pauseCurrent()
        super.onPause()
    }

    override fun onDestroy() {
        EmberRuntimeEvents.listener = null
        super.onDestroy()
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                NOTIFICATION_PERMISSION_REQUEST,
            )
        }
    }

    companion object {
        private const val CHANNEL_NAME = "com.ember.companion/core"
        private const val LIVE2D_VIEW_TYPE = "com.ember.companion/live2d"
        private const val NOTIFICATION_PERMISSION_REQUEST = 1001
    }
}

package com.ember.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

class EmberForegroundService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
        startForeground(NOTIFICATION_ID, buildNotification("正在启动本地核心…"))
        Log.i(TAG, "onCreate: 服务已创建")
        startCore()
        updateNotification()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_TOGGLE_CORE) {
            toggleCore()
            return START_STICKY
        }
        val reason = when {
            flags and START_FLAG_REDELIVERY != 0 -> "系统重投递"
            flags and START_FLAG_RETRY != 0 -> "系统重启(进程被杀后恢复)"
            else -> "正常启动/前台触发"
        }
        Log.i(TAG, "onStartCommand: reason=$reason running=$isRunning")
        if (!isRunning) {
            // 服务还在但核心已停止（例如通知里关过）：重新拉起。
            startCore()
            updateNotification()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy: 正在停止本地核心")
        stopCore()
        releaseWakeLock()
        Log.i(TAG, "onDestroy: 本地核心已停止")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startCore() {
        Log.i(TAG, "startCore: 正在启动 Python 核心")
        try {
            PythonRuntime.startEmber(
                this,
                PythonStreamCallback { eventJson -> handleRuntimeEvent(eventJson) },
            )
            isRunning = true
            acquireWakeLock()
            Log.i(TAG, "startCore: Python 核心已启动")
        } catch (error: Exception) {
            isRunning = false
            Log.e(TAG, "startCore: Python 核心启动失败: ${error.message}")
        }
    }

    private fun stopCore() {
        try {
            PythonRuntime.stopEmber(this)
            Log.i(TAG, "stopCore: Python 核心已停止")
        } catch (error: Exception) {
            Log.e(TAG, "stopCore: 停止 Python 核心失败: ${error.message}")
        } finally {
            isRunning = false
            releaseWakeLock()
        }
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val powerManager = getSystemService(PowerManager::class.java)
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "Ember:runtime",
        ).apply {
            setReferenceCounted(false)
            acquire()
        }
        Log.i(TAG, "已持有后台运行唤醒锁")
    }

    private fun releaseWakeLock() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        Log.i(TAG, "已释放唤醒锁")
    }

    private fun toggleCore() {
        if (isRunning) {
            Log.i(TAG, "toggleCore: 通过通知停止核心")
            stopCore()
        } else {
            Log.i(TAG, "toggleCore: 通过通知启动核心")
            startCore()
        }
        updateNotification()
    }

    private fun updateNotification() {
        getSystemService(NotificationManager::class.java).notify(
            NOTIFICATION_ID,
            buildNotification(
                if (isRunning) "本地核心运行中" else "本地核心已停止",
            ),
        )
    }

    private fun handleRuntimeEvent(eventJson: String) {
        EmberRuntimeEvents.emit(eventJson)
        val event = runCatching { JSONObject(eventJson) }.getOrNull() ?: return
        when (event.optString("type")) {
            "idle.message" -> {
                val speech = event.optString("speech").trim()
                if (speech.isNotEmpty()) {
                    Log.i(TAG, "后台收到主动消息: $speech")
                    showMessageNotification(speech)
                }
            }
            "finished" -> {
                if (!EmberRuntimeEvents.appInForeground) {
                    val speech = event.optString("speech").trim()
                    if (speech.isNotEmpty()) {
                        Log.i(TAG, "后台对话回复完成: $speech")
                        showMessageNotification(speech)
                    }
                }
            }
            "idle.error" -> {
                getSystemService(NotificationManager::class.java).notify(
                    NOTIFICATION_ID,
                    buildNotification("空闲演化暂时失败，核心仍在运行"),
                )
            }
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val runtimeChannel = NotificationChannel(
            CHANNEL_ID,
            "Ember 持续运行",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "显示本地 Ember 核心的运行状态"
            setShowBadge(false)
        }
        val messageChannel = NotificationChannel(
            MESSAGE_CHANNEL_ID,
            "依鸣的新消息",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "依鸣主动发来的新消息"
            enableVibration(true)
            setShowBadge(true)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannels(
            listOf(runtimeChannel, messageChannel),
        )
    }

    private fun showMessageNotification(speech: String) {
        val notificationId = nextMessageNotificationId.incrementAndGet()
        val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
            ?: Intent(this, MainActivity::class.java)
        val contentIntent = PendingIntent.getActivity(
            this,
            notificationId,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, MESSAGE_CHANNEL_ID)
            .setSmallIcon(applicationInfo.icon)
            .setContentTitle("依鸣")
            .setContentText(speech)
            .setStyle(NotificationCompat.BigTextStyle().bigText(speech))
            .setCategory(Notification.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setOnlyAlertOnce(false)
            .setContentIntent(contentIntent)
            .build()
        getSystemService(NotificationManager::class.java).notify(
            notificationId,
            notification,
        )
    }

    private fun buildNotification(content: String) =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(applicationInfo.icon)
            .setContentTitle("Ember")
            .setContentText(content)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .addAction(
                NotificationCompat.Action(
                    android.R.drawable.ic_popup_sync,
                    if (isRunning) "运行中 · 点击停止" else "已停止 · 点击启动",
                    toggleCorePendingIntent(),
                ),
            )
            .setContentIntent(
                PendingIntent.getActivity(
                    this,
                    0,
                    packageManager.getLaunchIntentForPackage(packageName),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                ),
            )
            .build()

    private fun toggleCorePendingIntent(): PendingIntent {
        val intent = Intent(this, EmberForegroundService::class.java).apply {
            action = ACTION_TOGGLE_CORE
        }
        return PendingIntent.getService(
            this,
            1,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    companion object {
        private const val CHANNEL_ID = "ember_runtime"
        private const val MESSAGE_CHANNEL_ID = "ember_messages"
        private const val NOTIFICATION_ID = 1001
        private const val ACTION_TOGGLE_CORE =
            "com.ember.companion.action.TOGGLE_CORE"
        private val nextMessageNotificationId = AtomicInteger(2000)

        @Volatile
        var isRunning: Boolean = false
            private set
        private const val TAG = "EmberService"
    }
}

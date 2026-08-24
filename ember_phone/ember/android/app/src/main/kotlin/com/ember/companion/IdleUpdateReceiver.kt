package com.ember.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import android.util.Log
import org.json.JSONObject

/** 到点后短暂执行一次空闲更新；需要说话时由服务推送通知。 */
class IdleUpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != IdleUpdateScheduler.ACTION_IDLE_UPDATE) return
        val powerManager =
            context.getSystemService(Context.POWER_SERVICE) as PowerManager
        val wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "Ember:idle-update",
        ).apply { setReferenceCounted(false) }
        wakeLock.acquire(120_000L) // 单次更新最长 2 分钟
        val pendingResult = goAsync()
        Thread {
            try {
                val raw = PythonRuntime.runIdleUpdate(context)
                val result = runCatching { JSONObject(raw) }.getOrNull()
                if (result?.optBoolean("started", false) == true) {
                    Log.i(TAG, "空闲更新完成")
                } else {
                    Log.i(TAG, "空闲更新未执行: ${result?.optString("reason") ?: raw}")
                }
            } catch (error: Exception) {
                Log.e(TAG, "空闲更新异常: ${error.message}")
            } finally {
                val delay = runCatching {
                    PythonRuntime.nextIdleDelay(context).trim().toDouble()
                }.getOrNull()
                if (delay != null && delay > 0) {
                    IdleUpdateScheduler.schedule(context, delay)
                }
                pendingResult.finish()
                if (wakeLock.isHeld) wakeLock.release()
            }
        }.start()
    }

    companion object {
        private const val TAG = "EmberService"
    }
}

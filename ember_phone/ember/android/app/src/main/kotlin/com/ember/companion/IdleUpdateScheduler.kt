package com.ember.companion

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/** 计划任务调度器：算好下次空闲更新时间，到点用闹钟唤醒执行。 */
object IdleUpdateScheduler {
    const val ACTION_IDLE_UPDATE = "com.ember.companion.action.IDLE_UPDATE"
    private const val TAG = "EmberService"

    fun schedule(context: Context, delaySeconds: Double) {
        if (delaySeconds <= 0 || delaySeconds > 24 * 3600) return
        val alarmManager =
            context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val triggerAt = System.currentTimeMillis() + (delaySeconds * 1000).toLong()
        val pendingIntent = pendingIntent(context)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            alarmManager.canScheduleExactAlarms()
        ) {
            // 已授权精确闹钟：到点立即唤醒，尽量贴近计划时间。
            alarmManager.setExactAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                triggerAt,
                pendingIntent,
            )
            Log.i(TAG, "已计划下次空闲更新(精确): ${delaySeconds}s")
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            // 未授权/低版本：非精确闹钟，Doze 下可能延迟到维护窗口。
            alarmManager.setAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                triggerAt,
                pendingIntent,
            )
            Log.i(TAG, "已计划下次空闲更新(非精确): ${delaySeconds}s")
        } else {
            alarmManager.set(AlarmManager.RTC_WAKEUP, triggerAt, pendingIntent)
            Log.i(TAG, "已计划下次空闲更新: ${delaySeconds}s")
        }
    }

    fun cancel(context: Context) {
        val alarmManager =
            context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        alarmManager.cancel(pendingIntent(context))
        Log.i(TAG, "已取消空闲更新计划")
    }

    private fun pendingIntent(context: Context): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            0,
            Intent(context, IdleUpdateReceiver::class.java).apply {
                action = ACTION_IDLE_UPDATE
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
}

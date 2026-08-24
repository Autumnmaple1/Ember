package com.ember.companion

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class PythonStreamCallback(private val emitter: (String) -> Unit) {
    @Suppress("unused")
    fun onEvent(eventJson: String) = emitter(eventJson)
}

object EmberRuntimeEvents {
    @Volatile
    var listener: ((String) -> Unit)? = null

    @Volatile
    var appInForeground: Boolean = false

    fun emit(eventJson: String) {
        listener?.invoke(eventJson)
    }
}

object PythonRuntime {
    @Synchronized
    fun ensureStarted(context: Context): Python {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
        return Python.getInstance()
    }

    fun startEmber(context: Context, callback: PythonStreamCallback? = null): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "start_runtime",
            context.filesDir.absolutePath,
            context.cacheDir.absolutePath,
            callback,
        ).toString()
    }

    fun stopEmber(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("stop_runtime").toString()
    }

    fun status(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("get_status_json").toString()
    }

    fun getInitialSetup(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("get_initial_setup").toString()
    }

    fun saveInitialSetup(
        context: Context,
        configJson: String,
        stateJson: String,
    ): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry")
            .callAttr("save_initial_setup", configJson, stateJson)
            .toString()
    }

    fun generateInitialState(
        context: Context,
        persona: String,
        characterName: String,
        sceneHint: String,
    ): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry")
            .callAttr("generate_initial_state", persona, characterName, sceneHint)
            .toString()
    }

    fun setExternalIdleDriver(context: Context, enabled: Boolean): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry")
            .callAttr("set_external_idle_driver", enabled)
            .toString()
    }

    fun nextIdleDelay(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry")
            .callAttr("next_idle_delay")
            .toString()
    }

    fun runIdleUpdate(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry")
            .callAttr("run_idle_update")
            .toString()
    }

    fun getMemoryOverview(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("get_memory_overview").toString()
    }

    fun recordInteraction(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("record_interaction").toString()
    }

    fun setPad(
        context: Context,
        pleasure: Double,
        arousal: Double,
        dominance: Double,
    ): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "set_pad",
            pleasure,
            arousal,
            dominance,
        ).toString()
    }

    fun getLlmConfig(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("get_llm_config").toString()
    }

    fun updateAppConfig(context: Context, configJson: String): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "update_app_config",
            configJson,
        ).toString()
    }

    fun updateLlmConfig(
        context: Context,
        baseUrl: String,
        model: String,
        smallModel: String,
        apiKey: String?,
        temperature: Double,
        stateUpdatesEnabled: Boolean,
    ): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "update_llm_config",
            baseUrl,
            model,
            smallModel,
            apiKey,
            temperature,
            stateUpdatesEnabled,
        ).toString()
    }

    fun sendMessage(context: Context, text: String): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("send_message", text).toString()
    }

    fun sendMessageStream(
        context: Context,
        text: String,
        imageSize: String?,
        callback: PythonStreamCallback,
    ): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "send_message_stream",
            text,
            imageSize,
            callback,
        ).toString()
    }

    fun getChatHistory(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("get_chat_history").toString()
    }

    fun clearChatHistory(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("clear_chat_history").toString()
    }

    fun listArchives(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("list_archives").toString()
    }

    fun createArchive(context: Context, name: String): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "create_archive",
            name,
        ).toString()
    }

    fun loadArchive(context: Context, archiveId: String): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "load_archive",
            archiveId,
        ).toString()
    }

    fun deleteArchive(context: Context, archiveId: String): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr(
            "delete_archive",
            archiveId,
        ).toString()
    }

    fun pythonInfo(context: Context): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("python_info").toString()
    }

    fun echo(context: Context, value: String): String {
        val python = ensureStarted(context)
        return python.getModule("mobile_entry").callAttr("echo", value).toString()
    }
}

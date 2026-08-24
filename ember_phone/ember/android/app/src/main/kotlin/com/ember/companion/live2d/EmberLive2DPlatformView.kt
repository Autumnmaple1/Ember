package com.ember.companion.live2d

import android.annotation.SuppressLint
import android.content.Context
import android.opengl.GLSurfaceView
import android.graphics.PixelFormat
import android.view.MotionEvent
import android.view.View
import com.live2d.demo.GLRenderer
import com.live2d.demo.JniBridgeJava
import io.flutter.plugin.platform.PlatformView

class EmberLive2DPlatformView(context: Context) : PlatformView {
    private val surfaceView = GLSurfaceView(context)
    private var disposed = false
    private var hostPaused = false

    init {
        // Cubism's Minimum sample owns global native singletons. Dispose any
        // stale PlatformView synchronously before a resumed Flutter tree
        // creates the replacement view.
        current?.dispose()
        JniBridgeJava.setContext(context)
        JniBridgeJava.nativeOnStart()
        surfaceView.setEGLContextClientVersion(2)
        surfaceView.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        surfaceView.holder.setFormat(PixelFormat.TRANSLUCENT)
        surfaceView.setBackgroundColor(android.graphics.Color.TRANSPARENT)
        surfaceView.setRenderer(GLRenderer())
        surfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        installTouchHandler()
        current = this
        // Flutter may create this PlatformView after Activity.onResume has
        // already fired. In that case resumeCurrent() could not see the view,
        // so start the GLSurfaceView render thread here as well.
        surfaceView.onResume()
    }

    override fun getView(): View = surfaceView

    override fun dispose() {
        if (disposed) return
        disposed = true
        if (!hostPaused) {
            surfaceView.onPause()
            JniBridgeJava.nativeOnPause()
        }
        JniBridgeJava.nativeOnStop()
        JniBridgeJava.nativeOnDestroy()
        if (current === this) current = null
    }

    fun onHostResume() {
        if (!disposed && hostPaused) {
            surfaceView.onResume()
            hostPaused = false
        }
    }

    fun onHostPause() {
        if (!disposed && !hostPaused) {
            surfaceView.onPause()
            JniBridgeJava.nativeOnPause()
            hostPaused = true
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun installTouchHandler() {
        surfaceView.setOnTouchListener { _, event ->
            val action = event.actionMasked
            val x = event.x
            val y = event.y
            surfaceView.queueEvent {
                when (action) {
                    MotionEvent.ACTION_DOWN -> JniBridgeJava.nativeOnTouchesBegan(x, y)
                    MotionEvent.ACTION_MOVE -> JniBridgeJava.nativeOnTouchesMoved(x, y)
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                        JniBridgeJava.nativeOnTouchesEnded(x, y)
                }
            }
            true
        }
    }

    companion object {
        @Volatile
        private var current: EmberLive2DPlatformView? = null

        fun resumeCurrent() = current?.onHostResume()
        fun pauseCurrent() = current?.onHostPause()

    }
}

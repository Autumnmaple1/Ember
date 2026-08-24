/**
 * Copyright(c) Live2D Inc. All rights reserved.
 *
 * Use of this source code is governed by the Live2D Open Software license.
 */
package com.live2d.demo;

import android.content.Context;

import java.io.IOException;
import java.io.InputStream;

public final class JniBridgeJava {
    public static native void nativeOnStart();
    public static native void nativeOnPause();
    public static native void nativeOnStop();
    public static native void nativeOnDestroy();
    public static native void nativeOnSurfaceCreated();
    public static native void nativeOnSurfaceChanged(int width, int height);
    public static native void nativeOnDrawFrame();
    public static native void nativeOnTouchesBegan(float pointX, float pointY);
    public static native void nativeOnTouchesEnded(float pointX, float pointY);
    public static native void nativeOnTouchesMoved(float pointX, float pointY);

    public static void setContext(Context value) {
        context = value.getApplicationContext();
    }

    public static byte[] LoadFile(String filePath) {
        if (context == null) {
            return null;
        }
        try (InputStream fileData = context.getAssets().open(filePath)) {
            byte[] fileBuffer = new byte[fileData.available()];
            int offset = 0;
            while (offset < fileBuffer.length) {
                int read = fileData.read(fileBuffer, offset, fileBuffer.length - offset);
                if (read < 0) {
                    break;
                }
                offset += read;
            }
            return fileBuffer;
        } catch (IOException error) {
            error.printStackTrace();
            return null;
        }
    }

    public static void MoveTaskToBack() {
        // The Flutter host owns the Activity lifecycle, so native sample code
        // must never move or finish the task itself.
    }

    private static Context context;

    static {
        System.loadLibrary("EmberLive2D");
    }

    private JniBridgeJava() {}
}

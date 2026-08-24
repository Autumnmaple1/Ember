# Live2D JNI 桥：这些方法由原生库通过 JNI 名称直接查找调用，
# R8 无法感知到引用，release 混淆时一旦被删/改名就会 NoSuchMethodError。
-keep class com.live2d.demo.JniBridgeJava { *; }
-keep class com.live2d.demo.GLRenderer { *; }
-keep class com.ember.companion.live2d.EmberLive2DPlatformView { *; }
-keep class com.ember.companion.live2d.EmberLive2DViewFactory { *; }

# Chaquopy Python 运行时（插件自带规则之外的兜底）
-keep class com.chaquo.python.** { *; }

# Python → Kotlin 事件回调：Python 按方法名调用 onEvent，
# R8 一旦改名/删除就会抛 AttributeError: 'I' object has no attribute 'onEvent'。
-keep class com.ember.companion.PythonStreamCallback { *; }

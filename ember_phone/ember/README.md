# Ember Mobile

Flutter Android client for Ember. The current migration milestone validates the
following local execution chain:

```text
Flutter -> Kotlin MethodChannel -> Chaquopy CPython 3.11
                              \-> Android foreground service
```

The diagnostic screen is temporary. It will be replaced by the companion UI
after the embedded runtime, SQLite storage, and Cubism renderer are integrated.

## Requirements

- Flutter stable with Dart 3.12 or later
- Android SDK with API 24 or later
- JDK 17 or later; the local project currently uses Android Studio's JBR 21
- An arm64 Android device for runtime validation
- Cubism SDK for Native R5 for the next Live2D milestone (not committed here)

Chaquopy embeds Python 3.11 in the APK. A host Python 3.11 installation is
optional, but without one the build prints a warning and skips `.pyc`
precompilation.

## Validate

From this directory:

```powershell
flutter analyze
flutter test
flutter build apk --debug --flavor arm64 --target-platform android-arm64
```

The APK is written to:

```text
build/app/outputs/flutter-apk/app-arm64-debug.apk
```

On a device, use the diagnostic actions in this order:

1. **Check Python** confirms the embedded CPython version.
2. **Test bridge** confirms Flutter/Kotlin/Python calls.
3. **Start core** starts the foreground service and runtime worker.
4. Wait several seconds, then **Refresh status**; `tick_count` should increase.
5. Close and reopen the Flutter activity; the count should remain continuous.
6. **Stop core** terminates the worker and foreground service.

## Current boundaries

- FastAPI, Uvicorn, PostgreSQL, pgvector, and the desktop WebSocket API are not
  part of the mobile runtime.
- The current Python module is only a lifecycle probe. The real Brain,
  StateManager, memory adapters, and event stream are the next migration batch.
- Native Cubism files are intentionally absent until the SDK has been supplied
  under its own license terms.

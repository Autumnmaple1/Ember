import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
    id("com.chaquo.python")
}

val localProperties = Properties().apply {
    rootProject.file("local.properties").inputStream().use(::load)
}
val cubismSdkDir = requireNotNull(localProperties.getProperty("cubism.sdk.dir")) {
    "cubism.sdk.dir must be set in android/local.properties"
}
val chaquopyBuildPython = requireNotNull(
    localProperties.getProperty("chaquopy.buildPython"),
) {
    "chaquopy.buildPython must be set in android/local.properties"
}

android {
    namespace = "com.ember.companion"
    compileSdk = flutter.compileSdkVersion
ndkVersion = "26.3.11579264"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.ember.companion"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = 24
        targetSdk = flutter.targetSdkVersion
    versionCode = flutter.versionCode
    versionName = flutter.versionName

    // Cubism SDK 5-r.5 has no armeabi-v7a Core library. Restrict every
    // native build variant at the defaultConfig level, including CMake tasks
    // created by the Flutter Gradle plugin.
    ndk {
        abiFilters.clear()
        abiFilters += "arm64-v8a"
    }

    externalNativeBuild {
            cmake {
                arguments += "-DCUBISM_SDK_ROOT=${cubismSdkDir.replace('\\', '/')}"
                cppFlags += "-std=c++14"
            }
        }

    }

    flavorDimensions += "abi"
    productFlavors {
        create("arm64") {
            dimension = "abi"
            ndk {
                abiFilters += listOf("arm64-v8a")
            }
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    externalNativeBuild {
        cmake {
            path = file("CMakeLists.txt")
            version = "3.22.1"
        }
    }

    sourceSets.named("main") {
        assets.srcDir(file("$cubismSdkDir/Samples/Resources"))
        assets.srcDir(file("$cubismSdkDir/Samples/OpenGL/Shaders/StandardES"))
        assets.srcDir(file("$cubismSdkDir/Framework/src/Rendering/OpenGL/Shaders/StandardES"))
        assets.srcDir(rootProject.file("../../../frontend/public/models"))
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        buildPython(chaquopyBuildPython)
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

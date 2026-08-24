cmake_minimum_required(VERSION 3.22.1)

project(EmberLive2D LANGUAGES C CXX)

if(NOT DEFINED CUBISM_SDK_ROOT OR NOT EXISTS "${CUBISM_SDK_ROOT}/Framework")
  message(FATAL_ERROR "CUBISM_SDK_ROOT is missing or invalid: ${CUBISM_SDK_ROOT}")
endif()

set(APP_NAME EmberLive2D)
set(CORE_PATH "${CUBISM_SDK_ROOT}/Core")
set(FRAMEWORK_PATH "${CUBISM_SDK_ROOT}/Framework")
set(COMMON_SRC_PATH "${CUBISM_SDK_ROOT}/Samples/Common")
set(SAMPLE_CPP_PATH "${CUBISM_SDK_ROOT}/Samples/OpenGL/Demo/proj.android.cmake/Minimum/app/src/main/cpp")
set(STB_PATH "${CUBISM_SDK_ROOT}/Samples/OpenGL/thirdParty/stb")

# Build a project-local copy of the official Minimum sample so Ember can keep a
# fixed upper-body camera without modifying the installed Cubism SDK.
set(EMBER_SAMPLE_CPP_PATH "${CMAKE_CURRENT_BINARY_DIR}/EmberSample")
file(COPY "${SAMPLE_CPP_PATH}/" DESTINATION "${EMBER_SAMPLE_CPP_PATH}")
set(EMBER_MANAGER_SOURCE "${EMBER_SAMPLE_CPP_PATH}/LAppMinimumLive2DManager.cpp")
file(
  READ
  "${SAMPLE_CPP_PATH}/LAppMinimumLive2DManager.cpp"
  EMBER_MANAGER_SOURCE_CONTENT
)
string(
  REPLACE
  "    if (_viewMatrix)"
  "    projection.ScaleRelative(2.325f, 2.325f);\n    projection.TranslateRelative(0.0f, -0.5f);\n\n    if (_viewMatrix)"
  EMBER_MANAGER_SOURCE_CONTENT
  "${EMBER_MANAGER_SOURCE_CONTENT}"
)
file(WRITE "${EMBER_MANAGER_SOURCE}" "${EMBER_MANAGER_SOURCE_CONTENT}")

# The official sample clears with an opaque black alpha. Keep the SDK itself
# untouched and make only Ember's build-local delegate surface transparent.
set(EMBER_DELEGATE_SOURCE "${EMBER_SAMPLE_CPP_PATH}/LAppMinimumDelegate.cpp")
file(
  READ
  "${SAMPLE_CPP_PATH}/LAppMinimumDelegate.cpp"
  EMBER_DELEGATE_SOURCE_CONTENT
)
string(
  REPLACE
  "glClearColor(0.0f, 0.0f, 0.0f, 1.0f);"
  "glClearColor(0.0f, 0.0f, 0.0f, 0.0f);"
  EMBER_DELEGATE_SOURCE_CONTENT
  "${EMBER_DELEGATE_SOURCE_CONTENT}"
)
file(WRITE "${EMBER_DELEGATE_SOURCE}" "${EMBER_DELEGATE_SOURCE_CONTENT}")

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(Live2DCubismCore STATIC IMPORTED)
set_target_properties(
  Live2DCubismCore
  PROPERTIES
    IMPORTED_LOCATION "${CORE_PATH}/lib/android/${ANDROID_ABI}/libLive2DCubismCore.a"
    INTERFACE_INCLUDE_DIRECTORIES "${CORE_PATH}/include"
)

set(FRAMEWORK_SOURCE OpenGL)
add_subdirectory("${FRAMEWORK_PATH}" "${CMAKE_CURRENT_BINARY_DIR}/Framework")
target_compile_definitions(Framework PUBLIC CSM_TARGET_ANDROID_ES2)

add_library(${APP_NAME} SHARED)
add_subdirectory("${COMMON_SRC_PATH}" "${CMAKE_CURRENT_BINARY_DIR}/Common")
add_subdirectory("${EMBER_SAMPLE_CPP_PATH}" "${CMAKE_CURRENT_BINARY_DIR}/Sample")

target_link_libraries(
  ${APP_NAME}
  Framework
  Live2DCubismCore
  GLESv2
  log
)
target_include_directories(${APP_NAME} PRIVATE "${STB_PATH}")
target_link_options(${APP_NAME} PRIVATE "-Wl,-z,max-page-size=16384")

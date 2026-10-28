cmake_minimum_required(VERSION 3.20)
project(gk_demo LANGUAGES CXX)

# ---------------------------------------------------------------------------
# CMake 构建路线(如果你不想装 SCons 就用这个)
#
# 前置:把 godot-cpp 放在 ../godot-cpp(或设置 GODOT_CPP_PATH 环境变量)
#       godot-cpp 官方目前最高分支是 4.5(没有 4.6 / 4.7 分支)，
#       所以固定用 4.5;它编出的库已在 Godot 4.7.2 上实测可用。
#
# 用法:
#   cmake -B build -DGODOT_CPP_PATH=../godot-cpp
#   cmake --build build --config Debug
# ---------------------------------------------------------------------------

set(GODOT_CPP_PATH "$ENV{GODOT_CPP_PATH}" CACHE PATH "godot-cpp 目录")
if(NOT GODOT_CPP_PATH)
    set(GODOT_CPP_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../godot-cpp")
endif()

if(NOT EXISTS "${GODOT_CPP_PATH}/CMakeLists.txt")
    message(FATAL_ERROR
        "找不到 godot-cpp: ${GODOT_CPP_PATH}\n"
        "请先下载对应版本:\n"
        "  git clone --depth 1 -b 4.5 https://github.com/godotengine/godot-cpp\n"
        "或用 -DGODOT_CPP_PATH=<路径> 指定位置")
endif()

add_subdirectory(${GODOT_CPP_PATH} godot-cpp)

add_library(gk_demo SHARED
    src/register_types.cpp
    src/gk_character.cpp
)

target_include_directories(gk_demo PRIVATE src)
target_link_libraries(gk_demo PRIVATE godot-cpp)

# 产物名必须和 gk_demo.gdextension 里的路径对得上:
#   windows.debug.x86_64 = "res://bin/gk_demo.windows.debug.x86_64.dll"
set_target_properties(gk_demo PROPERTIES
    PREFIX ""
    OUTPUT_NAME "gk_demo"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
)
foreach(config Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER ${config} CONFIG_UPPER)
    set_target_properties(gk_demo PROPERTIES
        OUTPUT_NAME_${CONFIG_UPPER} "gk_demo.windows.${config}.x86_64"
    )
endforeach()

message(STATUS "godot-cpp: ${GODOT_CPP_PATH}")
message(STATUS "输出目录: ${CMAKE_CURRENT_SOURCE_DIR}/bin")

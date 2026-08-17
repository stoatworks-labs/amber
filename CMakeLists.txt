cmake_minimum_required(VERSION 3.20)

project(Amber
    VERSION 0.1.0
    LANGUAGES C CXX
)

set(AMBER_VERSION "0.1.0")

# ---------------------------------------------------------------------------
# Architecture.
#
# The fleet ships universal bundles so one file loads in both the Apple Silicon
# and the Intel build of Resolume, and CMake latches the architecture list when
# the first target is created -- setting it later is silently ignored and
# produces an arm64-only binary the build log still calls a success.
#
# amber is arm64-only for now, deliberately. A universal build needs the Rust
# staticlib built for BOTH targets and lipo'd together, which is a real amount
# of machinery for a plugin that has never been loaded into Resolume even once.
# Arena on the development machine runs native arm64. Revisit before release.
#
# Verify with `lipo -archs`, never with the build log.
# ---------------------------------------------------------------------------
if(APPLE AND NOT CMAKE_OSX_ARCHITECTURES)
    set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "" FORCE)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Apple deprecated the whole OpenGL API in 10.14 and FFGL's plugin interface is
# still OpenGL. There is no alternative API to move to, so silencing it is not
# ignoring a problem.
if(APPLE)
    add_compile_definitions(GL_SILENCE_DEPRECATION)
endif()

# windows.h defines min and max as macros, which turns every std::min into
# `std::(...)` and fails with an error naming neither min nor windows.h.
if(WIN32)
    add_compile_definitions(NOMINMAX)
endif()

if(MSVC)
    add_compile_options(/utf-8)
endif()

# ---------------------------------------------------------------------------
# The Rust core.
#
# cargo is invoked as a custom command rather than through a CMake/Rust
# integration package, so there is nothing to install and the same command
# works by hand. The staticlib is enormous unstripped (~185MB of Ruffle and
# wgpu); the linker discards the great majority of it.
# ---------------------------------------------------------------------------
set(AMBER_CORE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/source/amber_core)

if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(AMBER_CARGO_PROFILE_DIR debug)
    set(AMBER_CARGO_FLAGS "")
else()
    set(AMBER_CARGO_PROFILE_DIR release)
    set(AMBER_CARGO_FLAGS --release)
endif()

set(AMBER_CORE_LIB
    ${AMBER_CORE_DIR}/target/${AMBER_CARGO_PROFILE_DIR}/libamber_core.a)

add_custom_command(
    OUTPUT ${AMBER_CORE_LIB}
    COMMAND cargo build ${AMBER_CARGO_FLAGS}
    WORKING_DIRECTORY ${AMBER_CORE_DIR}
    # Depending on the sources rather than always rerunning keeps an unchanged
    # Rust tree from relinking a 185MB archive on every C++ edit.
    DEPENDS
        ${AMBER_CORE_DIR}/src/lib.rs
        ${AMBER_CORE_DIR}/src/player.rs
        ${AMBER_CORE_DIR}/Cargo.toml
    COMMENT "Building amber_core (Ruffle) with cargo"
    VERBATIM
)
add_custom_target(amber_core_build DEPENDS ${AMBER_CORE_LIB})

# ---------------------------------------------------------------------------
# The plugin.
#
# The FFGL SDK sources are compiled straight into this target rather than into
# a static library of their own. `CFFGLPluginInfo` registers the plugin from a
# file-scope constructor that nothing references by name, so from a STATIC
# archive the linker may drop the whole translation unit -- yielding a bundle
# that loads, exports plugMain, and reports that it contains no plugins.
# Building everything into one target removes the archive, and with it the
# trap. (cartridge solves the same problem with an OBJECT library.)
# ---------------------------------------------------------------------------
set(FFGL_DIR ${CMAKE_CURRENT_SOURCE_DIR}/external/ffgl/source/lib)

# FFGLSDK.cpp is an UMBRELLA: it `#include`s every other .cpp in the SDK. It is
# therefore the only one that may be compiled -- adding the rest alongside it
# defines every symbol twice and the link fails with several hundred duplicates.
set(FFGL_SOURCES ${FFGL_DIR}/FFGLSDK.cpp)

add_library(AmberSource MODULE
    source/Plugin.cpp
    source/SourcePlugin.cpp
    ${FFGL_SOURCES}
)

target_include_directories(AmberSource PRIVATE
    ${FFGL_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/source
)

target_compile_definitions(AmberSource PRIVATE
    AMBER_VERSION="${AMBER_VERSION}"
)

add_dependencies(AmberSource amber_core_build)

target_link_libraries(AmberSource PRIVATE ${AMBER_CORE_LIB})

if(APPLE)
    # wgpu needs the Metal stack; FFGL needs OpenGL. Ruffle's dependency tree
    # also reaches CoreText/CoreGraphics for font handling and IOKit/Security
    # through the standard library.
    target_link_libraries(AmberSource PRIVATE
        "-framework OpenGL"
        "-framework Metal"
        "-framework MetalKit"
        "-framework QuartzCore"
        "-framework CoreGraphics"
        "-framework CoreText"
        "-framework CoreFoundation"
        "-framework Foundation"
        "-framework AppKit"
        "-framework IOKit"
        "-framework IOSurface"
        "-framework Security"
        "-framework SystemConfiguration"
    )

    set_target_properties(AmberSource PROPERTIES
        BUNDLE TRUE
        BUNDLE_EXTENSION "bundle"
        OUTPUT_NAME "Amber"
        MACOSX_BUNDLE_BUNDLE_NAME "Amber"
        MACOSX_BUNDLE_GUI_IDENTIFIER "com.stoatworks.amber"
        MACOSX_BUNDLE_BUNDLE_VERSION "${AMBER_VERSION}"
        MACOSX_BUNDLE_SHORT_VERSION_STRING "${AMBER_VERSION}"
    )
else()
    set_target_properties(AmberSource PROPERTIES
        OUTPUT_NAME "Amber"
        PREFIX ""
    )
endif()

# ---------------------------------------------------------------------------
# Install into Resolume's plugin directory.
# ---------------------------------------------------------------------------
if(APPLE)
    install(TARGETS AmberSource
        DESTINATION "$ENV{HOME}/Library/Graphics/FreeFrame Plug-Ins")
endif()

# ---------------------------------------------------------------------------
# ambergl -- the plugin class in a real GL context, without Resolume.
#
# Links Plugin.cpp directly rather than loading the bundle, so it tests the
# render path rather than the registration. `oxbow probe` covers registration,
# which is the half a directly-linked harness cannot see.
# ---------------------------------------------------------------------------
option(AMBER_BUILD_TOOLS "Build the verification harness" ON)

if(AMBER_BUILD_TOOLS AND APPLE)
    add_executable(ambergl
        tools/ambergl/main.cpp
        source/Plugin.cpp
        ${FFGL_SOURCES}
    )
    target_include_directories(ambergl PRIVATE
        ${FFGL_DIR}
        ${CMAKE_CURRENT_SOURCE_DIR}/source
    )
    target_compile_definitions(ambergl PRIVATE AMBER_VERSION="${AMBER_VERSION}")
    add_dependencies(ambergl amber_core_build)
    target_link_libraries(ambergl PRIVATE
        ${AMBER_CORE_LIB}
        "-framework OpenGL"
        "-framework Metal"
        "-framework MetalKit"
        "-framework QuartzCore"
        "-framework CoreGraphics"
        "-framework CoreText"
        "-framework CoreFoundation"
        "-framework Foundation"
        "-framework AppKit"
        "-framework IOKit"
        "-framework IOSurface"
        "-framework Security"
        "-framework SystemConfiguration"
    )
endif()

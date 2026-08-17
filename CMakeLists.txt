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
# Universal by default so one bundle loads in both the Apple Silicon and the
# Intel build of Resolume. -DAMBER_UNIVERSAL=OFF gives a much faster arm64-only
# dev build -- the Rust half has to be compiled twice for a universal one, and
# Ruffle is not small.
#
# Verify with `lipo -archs`, never with the build log.
# ---------------------------------------------------------------------------
option(AMBER_UNIVERSAL "Build a universal macOS binary (arm64 + x86_64)" ON)

if(APPLE AND NOT CMAKE_OSX_ARCHITECTURES)
    if(AMBER_UNIVERSAL)
        set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "" FORCE)
    else()
        set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "" FORCE)
    endif()
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

set(AMBER_CORE_SOURCES
    ${AMBER_CORE_DIR}/src/lib.rs
    ${AMBER_CORE_DIR}/src/player.rs
    ${AMBER_CORE_DIR}/Cargo.toml)

if(APPLE AND AMBER_UNIVERSAL)
    # cargo cannot emit a universal staticlib, so each architecture is built
    # separately and `lipo -create` glues the two archives together. The linker
    # then picks the right slice per architecture while building the universal
    # bundle. Doing it the other way round -- one cargo run, lipo at the end --
    # is not possible: there is no universal target triple.
    set(AMBER_CORE_LIB ${CMAKE_CURRENT_BINARY_DIR}/libamber_core_universal.a)
    set(AMBER_CORE_ARM ${AMBER_CORE_DIR}/target/aarch64-apple-darwin/${AMBER_CARGO_PROFILE_DIR}/libamber_core.a)
    set(AMBER_CORE_X86 ${AMBER_CORE_DIR}/target/x86_64-apple-darwin/${AMBER_CARGO_PROFILE_DIR}/libamber_core.a)

    add_custom_command(
        OUTPUT ${AMBER_CORE_LIB}
        COMMAND cargo build ${AMBER_CARGO_FLAGS} --target aarch64-apple-darwin
        COMMAND cargo build ${AMBER_CARGO_FLAGS} --target x86_64-apple-darwin
        COMMAND lipo -create ${AMBER_CORE_ARM} ${AMBER_CORE_X86} -output ${AMBER_CORE_LIB}
        WORKING_DIRECTORY ${AMBER_CORE_DIR}
        DEPENDS ${AMBER_CORE_SOURCES}
        COMMENT "Building amber_core (Ruffle) for arm64 and x86_64, then lipo"
        VERBATIM
    )
else()
    # An explicit cargo target triple, when the host default is not what is
    # wanted. The Windows build VM is ARM64 Windows, so a bare `cargo build`
    # there produces an ARM64 staticlib while CMake is building an x64 DLL --
    # which links with nothing but LNK4272 ("library machine type 'ARM64'
    # conflicts with target machine type 'x64'") followed by every symbol
    # unresolved. cargo puts a cross-compiled artefact under target/<triple>/,
    # so the path has to change with it.
    set(AMBER_CARGO_TARGET "" CACHE STRING "cargo target triple (blank = host default)")

    if(AMBER_CARGO_TARGET STREQUAL "")
        set(AMBER_CARGO_TARGET_DIR ${AMBER_CORE_DIR}/target/${AMBER_CARGO_PROFILE_DIR})
        set(AMBER_CARGO_TARGET_ARG "")
    else()
        set(AMBER_CARGO_TARGET_DIR
            ${AMBER_CORE_DIR}/target/${AMBER_CARGO_TARGET}/${AMBER_CARGO_PROFILE_DIR})
        set(AMBER_CARGO_TARGET_ARG --target ${AMBER_CARGO_TARGET})
    endif()

    # cargo names a staticlib `libfoo.a` everywhere except MSVC, where it is
    # `foo.lib`. Getting this wrong produces a configure that succeeds and a
    # link that cannot find the library.
    if(MSVC)
        set(AMBER_CORE_LIB ${AMBER_CARGO_TARGET_DIR}/amber_core.lib)
    else()
        set(AMBER_CORE_LIB ${AMBER_CARGO_TARGET_DIR}/libamber_core.a)
    endif()

    add_custom_command(
        OUTPUT ${AMBER_CORE_LIB}
        COMMAND cargo build ${AMBER_CARGO_FLAGS} ${AMBER_CARGO_TARGET_ARG}
        WORKING_DIRECTORY ${AMBER_CORE_DIR}
        # Depending on the sources rather than always rerunning keeps an
        # unchanged Rust tree from relinking a 185MB archive on every C++ edit.
        DEPENDS ${AMBER_CORE_SOURCES}
        COMMENT "Building amber_core (Ruffle) with cargo"
        VERBATIM
    )
endif()

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

if(WIN32)
    # FFGL's headers `#include <GL/glew.h>` on Windows, where there is no system
    # OpenGL header set the way macOS has one. GLEW comes from vcpkg, static so
    # the plugin does not acquire an external glew32.dll runtime dependency that
    # an operator would have to install alongside it. The `-md` half of
    # `x64-windows-static-md` keeps the dynamic CRT, which is what the rest of
    # the toolchain uses.
    find_package(GLEW REQUIRED)
    target_link_libraries(AmberSource PRIVATE GLEW::GLEW)

    # The Rust standard library and wgpu's D3D12 backend both pull in system
    # libraries that nothing in the C++ half references, so the linker has to be
    # told about them explicitly. Omitting any one of these fails at link time
    # with a wall of unresolved externals from inside Rust, which reads as a
    # broken staticlib rather than a missing -l.
    target_link_libraries(AmberSource PRIVATE
        opengl32
        ws2_32 userenv bcrypt ntdll advapi32 secur32
        d3d12 dxgi d3dcompiler
        propsys runtimeobject
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

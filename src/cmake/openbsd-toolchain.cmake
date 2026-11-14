# CMake toolchain file for the OpenBSD/amd64 cross toolchain.
#   cmake -DCMAKE_TOOLCHAIN_FILE=<prefix>/share/openbsd-cross/openbsd-toolchain.cmake \
#         -DOPENBSD_FLAVOR=8.0 ...
# OPENBSD_FLAVOR: release number (e.g. 8.0) or "current". Wrappers are looked up in
# OPENBSD_CROSS_BIN, default: the "bin" directory next to this file's install prefix.

if(NOT DEFINED OPENBSD_FLAVOR)
  if(DEFINED ENV{OPENBSD_FLAVOR})
    set(OPENBSD_FLAVOR "$ENV{OPENBSD_FLAVOR}")
  else()
    message(FATAL_ERROR "Set OPENBSD_FLAVOR (e.g. 8.0 or current)")
  endif()
endif()
if(NOT DEFINED OPENBSD_CROSS_BIN)
  if(DEFINED ENV{OPENBSD_CROSS_BIN})
    set(OPENBSD_CROSS_BIN "$ENV{OPENBSD_CROSS_BIN}")
  else()
    get_filename_component(OPENBSD_CROSS_BIN "${CMAKE_CURRENT_LIST_DIR}/../../bin" ABSOLUTE)
  endif()
endif()
# Try-compile projects re-read this file; forward the settings to them.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES OPENBSD_FLAVOR OPENBSD_CROSS_BIN)

set(_t "${OPENBSD_CROSS_BIN}/x86_64-unknown-openbsd${OPENBSD_FLAVOR}")
if(NOT EXISTS "${_t}-cc")
  message(FATAL_ERROR "OpenBSD cross wrapper not found: ${_t}-cc")
endif()
execute_process(COMMAND "${_t}-sysroot" OUTPUT_VARIABLE _sysroot OUTPUT_STRIP_TRAILING_WHITESPACE
                RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0)
  message(FATAL_ERROR "cannot locate the ${OPENBSD_FLAVOR} sysroot")
endif()

set(CMAKE_SYSTEM_NAME OpenBSD)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_SYSROOT "${_sysroot}")
set(CMAKE_C_COMPILER "${_t}-cc")
set(CMAKE_CXX_COMPILER "${_t}-c++")
set(CMAKE_AR "${_t}-ar" CACHE FILEPATH "")
set(CMAKE_RANLIB "${_t}-ranlib" CACHE FILEPATH "")
set(CMAKE_STRIP "${_t}-strip" CACHE FILEPATH "")
set(PKG_CONFIG_EXECUTABLE "${_t}-pkg-config" CACHE FILEPATH "")

# Packages fetched with pkg_fetch.py live in <sysroot>/usr/local.
set(CMAKE_FIND_ROOT_PATH "${_sysroot}" "${_sysroot}/usr/local")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

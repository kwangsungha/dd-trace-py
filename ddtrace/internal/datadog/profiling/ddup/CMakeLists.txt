cmake_minimum_required(VERSION 3.19)
project(ddup
    VERSION 0.1.0
    LANGUAGES CXX
)

# C++17
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Infer some basic properties about the build.  This is necessary because multiple
# extensions reuse this cmake file, so we need its artifacts to go in a consistent
# place
get_filename_component(ddup_BUILD_PARENT "${CMAKE_BINARY_DIR}/.." ABSOLUTE)
set(ddup_BUILD_DIR "${ddup_BUILD_PARENT}/libddup" PARENT_SCOPE)

# For now, FindLibdatadog requires these
message(STATUS "ddup cmake dir:" ${CMAKE_CURRENT_SOURCE_DIR}/cmake)
set(Datadog_BUILD_DIR "${ddup_BUILD_PARENT}/libdatadog")
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# Includes
include(CMakePackageConfigHelpers) # For generating the config file
include(FetchContent)
include(ExternalProject)
include(FindLibdatadog)
include(FindPython3)

# Send some of these vars back up to the parent
set(Datadog_INCLUDE_DIRS "${Datadog_INCLUDE_DIRS}" PARENT_SCOPE)

# Library sources
add_library(ddup SHARED
    src/exporter.cpp
    src/interface.cpp
)

target_include_directories(ddup PRIVATE
    include
    ${Datadog_INCLUDE_DIRS}
    ${Python3_INCLUDE_DIRS}
)
target_link_libraries(ddup PRIVATE
    ${Datadog_LIBRARIES}
)
set_target_properties(ddup PROPERTIES POSITION_INDEPENDENT_CODE ON)

set_target_properties(ddup
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${ddup_BUILD_DIR}"
)

# Assumed ddup_INSTALL_DIR is propagated from the parent
message(STATUS "ddup_INSTALL_DIR: ${ddup_INSTALL_DIR}")
install(TARGETS ddup
    DESTINATION ${ddup_INSTALL_DIR}
)

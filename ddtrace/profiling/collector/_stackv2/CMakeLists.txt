cmake_minimum_required(VERSION 3.19)

# We're building a Python extension and expect to be called from setup.py
set(EXTENSION_NAME "" CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})

# Set C++17 as required standard for the whole project.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Includes
include(FetchContent)
include(FindPython3)

# Find the Python interpreter
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)

# Add the external project
FetchContent_Declare(
    echion
    GIT_REPOSITORY "https://github.com/sanchda/echion.git"
    GIT_TAG "main"
)    

# Make sure the content is fetched
FetchContent_GetProperties(echion)
if(NOT echion_POPULATED)
  FetchContent_Populate(echion)
endif()

# echion needs to know the underlying platform; if it's given to us, use that
# else try to infer it
set(PLATFORM "Unknown" CACHE STRING "Target platform")
if(PLATFORM STREQUAL "Unknown")
    if(WIN32)
        set(PLATFORM "Windows")
    elseif(APPLE)
        set(PLATFORM "Darwin")
    elseif(UNIX)
        set(PLATFORM "Linux")
    endif()
endif()
if(PLATFORM STREQUAL "Windows")
    add_definitions(-DPL_WINDOWS)
elseif(PLATFORM STREQUAL "Darwin")
    add_definitions(-DPL_DARWIN)
elseif(PLATFORM STREQUAL "Linux")
    add_definitions(-DPL_LINUX)
endif()

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED
    _stack_v2.cpp
)

target_include_directories(${EXTENSION_NAME} PRIVATE
    ${Python3_INCLUDE_DIRS}
    ${echion_SOURCE_DIR}
    ${ddup_SOURCE_DIR}/include
)

# We want the extension name to be literal
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")

# We want the extension to be built with position-independent code
set_target_properties(${EXTENSION_NAME} PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Set the output directory for the built library
set_target_properties(${EXTENSION_NAME}
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

install(TARGETS ${EXTENSION_NAME} DESTINATION ${LIB_INSTALL_DIR})

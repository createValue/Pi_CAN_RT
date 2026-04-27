cmake_minimum_required(VERSION 3.16)
project(rpi_can_loop_rt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(rpi_can_loop_rt
    src/main.cpp
)

target_include_directories(rpi_can_loop_rt PRIVATE include)

target_compile_options(rpi_can_loop_rt PRIVATE
    -O2
    -Wall
    -Wextra
    -pthread
)

target_link_libraries(rpi_can_loop_rt
    pthread
)

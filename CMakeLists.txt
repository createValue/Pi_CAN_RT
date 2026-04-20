cmake_minimum_required(VERSION 3.10)
project(dual_can_rt_v1)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(include)

add_executable(dual_can_rt
    src/main.cpp
)

target_link_libraries(dual_can_rt
    pthread
)

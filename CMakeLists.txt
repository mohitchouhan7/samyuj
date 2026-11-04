cmake_minimum_required(VERSION 3.16)
project(samyuj_fast LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_library(sc_fast SHARED sc_fast.cpp)
target_compile_options(sc_fast PRIVATE -O3)
set_target_properties(sc_fast PROPERTIES OUTPUT_NAME "sc_fast" PREFIX "lib")

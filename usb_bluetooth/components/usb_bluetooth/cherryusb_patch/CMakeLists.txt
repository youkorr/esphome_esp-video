# An ESP-IDF component that compiles nothing and exists to run one edit.
#
# See patch.cmake for what it changes and why. The timing is the only subtle
# part: every component's CMakeLists is processed during CMake's CONFIGURE
# step, and the component manager has already downloaded its dependencies by
# then, while nothing is COMPILED until afterwards. So a header edited here is
# edited before any compiler reads it, whatever order the components are
# processed in.

idf_component_register()

set(PROJECT_DIR "${CMAKE_SOURCE_DIR}")
include("${CMAKE_CURRENT_LIST_DIR}/patch.cmake")

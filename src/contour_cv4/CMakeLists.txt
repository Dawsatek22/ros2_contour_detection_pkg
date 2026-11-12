cmake_minimum_required(VERSION 4.4.3) # current cmake version
project(contour_cv4) # project name

set(CMAKE_CXX_STANDARD 17) # current c++ standard
set(CMAKE_CXX_STANDARD_REQUIRED ON) # standard set on
if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(OpenCV 4.6 REQUIRED)
find_package(cv_bridge REQUIRED)
# below are the included directories
include_directories( ${cv_bridge_INCLUDE_DIRS} ${OpenCV_INCLUDE_DIRS}})
# below are the publisher node
add_executable(cam_pub src/camera_publisher4x.cpp) # camera publisher node
# below are the  subcriber node
add_executable(cont_sub src/contour_subscriber.cpp) # countour subscriber node

# add dependencies
ament_target_dependencies(cam_pub rclcpp sensor_msgs cv_bridge OpenCV)
ament_target_dependencies(cont_sub rclcpp sensor_msgs cv_bridge OpenCV)

target_link_libraries(cam_pub ${OPENCV_LIBS})
target_link_libraries(cont_sub ${OPENCV_LIBS})
install(TARGETS cam_pub cont_sub
DESTINATION lib/${PROJECT_NAME})
ament_package()

install(DIRECTORY launch
DESTINATION share/${PROJECT_NAME})
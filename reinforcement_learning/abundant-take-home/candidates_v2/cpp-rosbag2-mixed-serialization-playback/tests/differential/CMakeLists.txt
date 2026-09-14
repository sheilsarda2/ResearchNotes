cmake_minimum_required(VERSION 3.20)
project(mixed_formats_differential CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Built inside the verifier against the rebuilt overlay (/workspace/ws/install), never against a
# reference binary: the differential is between the two storage plugins of the submission itself.
find_package(rclcpp REQUIRED)
find_package(rmw REQUIRED)
find_package(rosbag2_storage REQUIRED)
find_package(rosbag2_cpp REQUIRED)
find_package(rosbag2_compression REQUIRED)
find_package(rosbag2_transport REQUIRED)
find_package(test_msgs REQUIRED)

add_executable(mixed_formats_differential mixed_formats_differential.cpp)
target_link_libraries(mixed_formats_differential
  rclcpp::rclcpp
  rmw::rmw
  rosbag2_storage::rosbag2_storage
  rosbag2_cpp::rosbag2_cpp
  rosbag2_compression::rosbag2_compression
  rosbag2_transport::rosbag2_transport
  ${test_msgs_TARGETS}
)

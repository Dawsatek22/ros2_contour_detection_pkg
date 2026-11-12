from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import SetEnvironmentVariable, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution


def generate_launch_description():
    
    pub = Node(
        package='contour_cv4'
        ,executable='cam_pub'
    )
    
    sub = Node(
            package='contour_cv4'
            ,executable='cont_sub'
        )
    
    
    return LaunchDescription([pub,sub])
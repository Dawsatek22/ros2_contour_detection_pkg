from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration


MCP_HOST = '127.0.0.1'
MCP_PORT = 9000


def generate_launch_description():
    launch_mcp_arg = DeclareLaunchArgument('launch_mcp_server', default_value='true')
    launch_rosbridge_arg = DeclareLaunchArgument('launch_rosbridge', default_value='true')

    mcp_proc = ExecuteProcess(
        cmd=['uvx', 'ros-mcp', '--transport', 'streamable-http', '--host', MCP_HOST, '--port', str(MCP_PORT)],
        condition=IfCondition(LaunchConfiguration('launch_mcp_server')),
        name='mcp_server',
        output='screen'
    )

    rosbridge_proc = ExecuteProcess(
        cmd=['ros2', 'launch', 'rosbridge_server', 'rosbridge_websocket_launch.xml', 'delay_between_messages:=0.0'],
        condition=IfCondition(LaunchConfiguration('launch_rosbridge')),
        name='rosbridge_websocket',
        output='screen'
    )

    return LaunchDescription([
        launch_mcp_arg,
        launch_rosbridge_arg,
        mcp_proc,
        rosbridge_proc,
    ])

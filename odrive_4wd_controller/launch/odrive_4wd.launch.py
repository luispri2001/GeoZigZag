from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("config_dir"),
            DeclareLaunchArgument("limit_profile", default_value="normal_operation"),
            Node(
                package="odrive_4wd_controller",
                executable="odrive_4wd_node",
                name="odrive_4wd_controller",
                output="screen",
                parameters=[
                    {
                        "config_dir": LaunchConfiguration("config_dir"),
                        "limit_profile": LaunchConfiguration("limit_profile"),
                        "hardware_mode": "four_wheel",
                    }
                ],
            ),
        ]
    )

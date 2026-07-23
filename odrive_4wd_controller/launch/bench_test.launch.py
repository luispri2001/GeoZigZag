from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    config_dir = str(
        Path(get_package_share_directory("odrive_4wd_controller")) / "config"
    )
    return LaunchDescription(
        [
            Node(
                package="odrive_4wd_controller",
                executable="odrive_4wd_node",
                name="odrive_4wd_controller",
                output="screen",
                parameters=[
                    {
                        "config_dir": config_dir,
                        "limit_profile": "bench_test",
                        "hardware_mode": "bench_2wd",
                    }
                ],
            )
        ]
    )

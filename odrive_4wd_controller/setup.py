from glob import glob
from setuptools import find_packages, setup

package_name = "odrive_4wd_controller"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=("test",)),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/docs", glob("docs/*.md") + glob("docs/*.json")),
    ],
    install_requires=["setuptools", "PyYAML"],
    tests_require=["pytest"],
    zip_safe=True,
    maintainer="Luis",
    maintainer_email="luis@robotica.local",
    description="Fail-safe serial-bound ODrive controller for four-wheel skid-steer robots.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "odrive_4wd_node = odrive_4wd_controller.ros_node:main",
            "odrive_discovery = odrive_4wd_controller.tools:discovery_main",
            "identify_wheels = odrive_4wd_controller.tools:identify_main",
            "test_single_wheel = odrive_4wd_controller.tools:test_single_main",
            "test_side = odrive_4wd_controller.tools:test_side_main",
            "test_drivetrain = odrive_4wd_controller.tools:test_drivetrain_main",
            "calculate_limits = odrive_4wd_controller.tools:calculate_main",
            "export_configuration = odrive_4wd_controller.tools:export_main",
        ]
    },
)

# Upstream ROS/Gazebo integration audit

Audit date: 2026-07-03.

This document records verified upstream behavior. It is an integration plan,
not a claim that the complete simulator was executed from this repository.

## Geo2Gazebo / Scenario Generation

Repository: `ssancd03/Geo2Gazebo`, inspected commit `77e4ea4`.

Verified entry points:

```bash
python bridge.py
streamlit run cornfield_generator.py
```

`bridge.py` is an interactive terrain pipeline that expects pre-acquired GLTF
and texture input and invokes Blender. `cornfield_generator.py` discovers
Gazebo worlds containing `<spherical_coordinates>`, draws planting polygons,
generates crop model includes, and writes a world and launch file.

Its GPS conversion uses the world latitude/longitude as `(0, 0)`, east as
Gazebo `x`, north as Gazebo `y`, then applies `<heading_deg>`. The crop stage is
currently coupled to `summit_cornfield` package paths and models. A future
adapter should call reusable functions while supplying explicit paths; it
should not copy the Streamlit application or rely on its home-directory
fallbacks.

## Wildboar simulation

Repository: `wildboar_doc`, inspected commit `a188bc0`.

Workspace setup is:

```bash
./setup_wildboar.sh
```

The script clones the Wildboar repositories, `summit_agriculture`, and
`simulation_assets`, installs dependencies with `rosdep`, and runs `colcon
build`. The actual simulator entry point was verified in `wildboar_gazebo`:

```bash
source /opt/ros/humble/setup.bash
source "$WILDBOAR_WS/install/setup.bash"
ros2 launch wildboar_gazebo field.launch.py \
  world:="/absolute/path/to/generated.world"
```

`field.launch.py` accepts either `world_name` or a full `world` path and then
includes `gazebo.launch.py`. The latter can optionally launch localization and
navigation and spawns the `wildboar` entity from `robot_description`.

## Jabali CropFollow

Repository: `luispri2001/jabali-cropfollow-ros2`, inspected commit `a1bacd6`.

Verified build and launch commands:

```bash
colcon build --symlink-install --packages-select cropfollow_pp
colcon test --packages-select cropfollow_pp
source install/setup.bash
ros2 launch cropfollow_pp cropfollow_pp.launch.py \
  use_sim_time:=true enable_motion:=false
```

The node consumes ZED RGB, camera information, and IMU data. It publishes a
reference path in `base_link`, debug/status topics, preview velocity, and
optionally `/cmd_vel`. It does **not** consume geographic waypoint files.

Consequently, two distinct downstream modes must not be conflated:

1. a Nav2/GPS waypoint consumer executes GeoZigzag's sparse exported route;
2. CropFollow uses simulated camera imagery to control motion within crop rows.

An end-to-end experiment needs verified simulated camera topics, camera
intrinsics/extrinsics, `/cmd_vel` remapping, TF frames, simulated time, row-end
switching, and an arbitration policy between waypoint navigation and
CropFollow. Those checks are not provided by the current software evaluation.

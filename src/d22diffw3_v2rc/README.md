# Build and run container
```
cd docker/iron # it can be other ROS version
bash run_basic.bash # build time about 1 hour. Have 20 GB of free space
```

After container is builded connect to it (for example with the Portainer program).


# Commands in container:
## Run gazebo with model
```
. ~/.profile
ros2 launch d22diffw3_v2rc gazebo.launch.py
```

## Run gazebo with model and ros2_controllers
```
. ~/.profile
ros2 launch d22diffw3_v2rc ros2_control.launch.py
```

## Choose of map
```
. ~/.profile
GZ_SIM_WORLD=empty ros2 launch d22diffw3_v2rc ros2_control.launch.py
GZ_SIM_WORLD=cars_and_trees ros2 launch d22diffw3_v2rc ros2_control.launch.py
GZ_SIM_WORLD=empty ros2 launch d22diffw3_v2rc gazebo.launch.py
GZ_SIM_WORLD=cars_and_trees ros2 launch d22diffw3_v2rc gazebo.launch.py
```
cars_and_trees map will download included objects from internet firt run (it can take time)

## Run agent:
It can be necessary to ajust specific_system_prompt variable in agent_node for your task for better execution.

### Run MCP serve and rosbridge
Agent will use MCP tools when he want and communicate to ROS2 via bridge.
```
. ~/.profile
ros2 launch d22diffw3_v2rc_agent mcp_server_and_rosbridge.launch.py
```

### Run agent with cloud Ollama model
##### Signin to Ollama cloud
Do command in Ollama container (1 time) and follow it`s response.
```
ollama signin
```

##### Run agent with custom camera every tick (good agent performance)
```
. ~/.profile
ros2 run d22diffw3_v2rc_agent agent_node \
  --ros-args \
    -p use_camera_every_tick:=true \
    -p camera_front:=/camera_front/compressed \
    -p model_name:=qwen3-vl:235b-instruct-cloud
```

### Run agent with local Ollama model (good agent performance)
Adjust camera topic with your.
```
. ~/.profile
ros2 run d22diffw3_v2rc_agent agent_node \
  --ros-args \
    -p use_camera_every_tick:=true \
    -p camera_front:=/camera_front/compressed \
    -p model_name:=qwen3-vl:8b-instruct
```

### Run agent with MCP tool for analize image (low performance)
Agent will use that tool when he want and tryng to understand correct camera topic.
```
. ~/.profile
ros2 run d22diffw3_v2rc_agent agent_node \
  --ros-args \
    -p model_name:=qwen3-vl:8b-instruct
```

# Rebuild ros package:
## command in host (in case of reexport from RobotCAD)
```
bash run_basic.bash -r
```
## command in container (in case of code correction)
```
. ~/.profile
colcon build --packages-select d22diffw3_v2rc

. ~/.profile
colcon build --packages-select d22diffw3_v2rc_agent
```

# Troubleshooting
Segmentation fault - posible reasons:
- using fixed joint as not fixed. Check all joints types.

rqt crash - posible reasons:
- not set velocity limits of controlled joints by joint_trajectory_controller (divide by zero error)
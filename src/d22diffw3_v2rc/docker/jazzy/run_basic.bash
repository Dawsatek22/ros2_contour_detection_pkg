#!/bin/bash

set -e

# Usage info
show_help() {
cat << EOF
Usage: ${0##*/} [-dhbrRo]
It makes project structure, installs git submodules, builds docker image, runs container.

    -h          display this help and exit
    -d          debug mode (port 5688:5688)
    -b          do all but without running container
    -r          rebuild ros package
    -R          rosdep install and rebuild ros package
    -o          additionally run Ollama container
EOF
}


# process params of script
while getopts dobrRih opt; do
    case $opt in
        h)
            show_help
            exit 0
            ;;
        d)
            debug=true
            echo 'DEBUG is requested.'
            ;;
        o)
            run_ollama_container=true
            ;;
        b)  
            only_build_image=true
            ;;
        r)  
            only_rebuild_ros_package=true
            ;;
        R)  
            only_rosdep_instal_and_rebuild_ros_package=true
            ;;
        i)  
            # getopts does not work in included call and instead used position arg $1
            ;;
        *)
            show_help >&2
            exit 1
            ;;
    esac
done


if [ "$1" = "-i" ] ; then
    included_run=true
    only_build_image=true
fi


ros_distro=jazzy
ros_distro_assembly=desktop
package_name=d22diffw3_v2rc

user_in_cont=$USER
[[ ! -z "$UID" ]] || UID=$(id -u $USER) # if empty then fill UID
GROUP=$(groups | awk '{print $1}')
GID=$(id -g $USER)

ros_container_name=ros2_${ros_distro}_${ros_distro_assembly}_basic_${package_name}
ros_image_basic=ros2:${ros_distro}_${ros_distro_assembly}_basic
ros_image_package=ros2:${ros_distro}_${ros_distro_assembly}_basic_${package_name}
path_to_ws_relatively_user_in_cont=ros2/ws
not_ros_packages_folder_name=not_ros_packages
path_to_not_ros_packages_rel_ws=/src/$package_name/modules/$not_ros_packages_folder_name
init_git=true


basedir=`dirname $0`
run_script_dir=`cd $basedir; pwd; cd - > /dev/null 2>&1;`


. ./../scripts/includes/make_paths.bash
. ./../scripts/includes/init_git.bash
. ./../scripts/includes/update_git_submodules.bash

# must be after make_paths.bash
package_path=$cont_path_ws/src/$package_name

cd $run_script_dir


if [ -d $modules_replacements_path ]; then
    ### Make replacements in git submodules
    rsync -rv --exclude="COLCON_IGNORE" --exclude '.gitkeep' $modules_replacements_path $modules_path
    ### End make replacements in git submodules
fi


if [ "$only_rebuild_ros_package" = true ] && [ -z "$included_run" ] ; then
    echo 'Rebuild ROS package started...'
    docker exec $ros_container_name /bin/bash -c "cd \$PATH_TO_WS && \
        . \$HOME/.profile && colcon build --packages-select $package_name"
    echo 'Rebuild ROS package finished.'
    echo 'Exit.'
    exit 0
fi


if [ "$only_rosdep_instal_and_rebuild_ros_package" = true ] && [ -z "$included_run" ] ; then
    echo 'Rosdep install and rebuild ROS package started...'
    docker exec $ros_container_name /bin/bash -c "cd \$PATH_TO_WS && \
        . \$HOME/.profile && sudo apt update && rosdep update \
        && sudo PIP_BREAK_SYSTEM_PACKAGES=1 rosdep install -y -r -q --from-paths src/$package_name --ignore-src --rosdistro \$ROS_DISTRO \
        && colcon build --packages-select $package_name"
    echo 'Rosdep install and rebuild ROS package finished.'
    echo 'Exit.'
    exit 0
fi


# build if image not exists
if [ -z "$(docker images -q $ros_image_basic 2> /dev/null)" ]; then
    echo 'Build basic docker image...'
    docker buildx build -t $ros_image_basic --shm-size=512m \
        --build-arg USER=$USER \
        --build-arg UID=$UID \
        --build-arg GROUP=$GROUP \
        --build-arg GID=$GID \
        --build-arg ROS_DISTRO_ARG=$ros_distro \
        --build-arg ROS_DISTRO_ASSEMBLY_ARG=$ros_distro_assembly \
        --file dockerfile_basic \
        .
fi


# build if image not exists
if [ -z "$(docker images -q $ros_image_package 2> /dev/null)" ]; then
    echo 'Build ros package docker image...'
    docker buildx build -t $ros_image_package --shm-size=512m \
        --build-arg PATH_TO_WS_RELATIVE_TO_USER=$path_to_ws_relatively_user_in_cont \
        --build-arg ROS_DISTRO_ARG=$ros_distro \
        --build-arg ROS_DISTRO_ASSEMBLY_ARG=$ros_distro_assembly \
        --build-arg RELATIVE_PATH_FROM_WS_TO_GIT=src/$package_name \
        --build-context git_root=$path_to_git_root \
        --file dockerfile_package \
        .
fi


if [ -z "$only_build_image" ] ; then 
    # Create a custom Docker network if it doesn't exist
    docker_network="ros_net"
    if [ -z "$(docker network ls --filter name=^${docker_network}$ --format '{{.Name}}')" ]; then
        echo "Creating Docker network: $docker_network"
        docker network create $docker_network
    fi

    if [ ! "$(docker ps -q -f name=^$ros_container_name$)" ]; then
        if [ "$(docker ps -aq -f status=exited -f name=^$ros_container_name$)" ]; then
            echo "Start existing $ros_container_name container..."
            docker start $ros_container_name
            echo "$ros_container_name container started."
            . ./../scripts/includes/call_container_shell.bash
            exit 0
        fi

        . ./../scripts/includes/make_build_dirs.bash
        . ./../scripts/includes/make_nvidia_run_options.bash

        # user for container
        # in regular docker will be used user from dockerfile (same as at host)
        # in rootless mode will be used root user
        user_option=''
        is_rootless_docker=$(docker info 2> /dev/null | grep rootless)
        if [ -n "$is_rootless_docker" ]; then
            echo -e "\nDetected rootless Docker. I use root user in container.\n"
            user_option='--user=root'
        fi

        echo 'Run docker container...'
        # run container

        debug_port=''
        debug_env=''
        localhost_address=''
        if [ "$debug" = true ] ; then
            debug_port='-p 5688:5688'
            debug_env='--env DEBUG=1'
            localhost_address='--add-host localhost=10.0.2.2'
        fi

        xhost +local:
        mount_options=',type=volume,volume-driver=local,volume-opt=type=none,volume-opt=o=bind'
        docker run -t -d --name=$ros_container_name \
            --mount dst=$cont_path_ws/build,volume-opt=device=$build_data_path/build$mount_options \
            --mount dst=$cont_path_ws/install,volume-opt=device=$build_data_path/install$mount_options \
            --mount dst=$cont_path_ws/log,volume-opt=device=$build_data_path/log$mount_options \
            --mount dst=/home/$user_in_cont/.ros/log,volume-opt=device=$build_data_path/ros2_system_logs$mount_options \
            --mount dst=$cont_path_ws$path_to_not_ros_packages_rel_ws,volume-opt=device=$build_data_path/$not_ros_packages_folder_name$mount_options \
            --volume=$path_to_ws/src:$cont_path_ws/src \
            --volume=$cont_path_ws$path_to_not_ros_packages_rel_ws \
            --volume="/tmp/.X11-unix:/tmp/.X11-unix:rw" \
            --privileged \
            $nvidia_options \
            --env DISPLAY=$DISPLAY \
            --env WAYLAND_DISPLAY=$WAYLAND_DISPLAY \
            --env XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR \
            --env PULSE_SERVER=$PULSE_SERVER \
            --env QT_X11_NO_MITSHM=1 \
            --shm-size=512m \
            --security-opt seccomp=unconfined \
            --cap-add IPC_OWNER \
            $debug_port \
            $debug_env \
            --network "$docker_network" \
            --add-host=host.docker.internal:host-gateway \
            $localhost_address \
            $user_option \
            $ros_image_package

            # -p 5680:5678 \ # debug port
            # not_ros_packages mounted (cont -> host) in package build folder and exluded from src bind.
            # Also not_ros_packages has some files replacement (host -> cont) after container runned.
            # This sothisticated bind because of not_ros_packages are compiled inside image
            # and not_ros_packages like px4 can be recompiled with new Airframe (when you change some in OVERCROSS project)
            # without docker rebuild. Just in container.
        xhost -


        . ./../scripts/includes/copy_modules_replacements_to_build.bash

        # Run Ollama agent container if requested
        if [ "$run_ollama_container" = true ]; then
            ollama_container_name="ollama"
            ollama_host_port=11435
            ollama_cont_port=11434
            ollama_image="ollama/ollama"
            ollama_model=qwen3-vl:8b-instruct

            # Use arrays to safely accumulate arguments
            docker_run_args=(
                --name "$ollama_container_name"
                --network "$docker_network"
                -v ollama:/root/.ollama
                -p "${ollama_host_port}:${ollama_cont_port}"
                --restart unless-stopped
            )

            # Detect GPU type
            has_nvidia=false
            has_amd=false

            if command -v nvidia-smi >/dev/null 2>&1; then
                has_nvidia=true
            elif [ -d /dev/dri ] && [ -e /dev/kfd ]; then
                has_amd=true
            fi

            # Configure for NVIDIA GPU
            if [ "$has_nvidia" = true ]; then
                echo "NVIDIA GPU detected. Using --gpus=all."
                docker_run_args+=(--gpus=all)
                # Optional Jetson support
                if grep -q "jetson" /proc/device-tree/model 2>/dev/null || [ -f /etc/nv_tegra_release ]; then
                    echo "Jetson platform detected. Setting JETSON_JETPACK=6."
                    docker_run_args+=(-e JETSON_JETPACK=6)
                fi
            # Configure for AMD GPU (ROCm)
            elif [ "$has_amd" = true ]; then
                echo "AMD GPU (ROCm) detected. Using ROCm image and device mounts."
                ollama_image="ollama/ollama:rocm"
                docker_run_args+=(--device /dev/kfd --device /dev/dri)
            else
                echo "No GPU detected. Running on CPU only."
            fi

            # Optional: enable Vulkan (uncomment if needed)
            # docker_run_args+=(-e OLLAMA_VULKAN=1)

            # Check if container exists
            if [ -z "$(docker ps -q -f name=^${ollama_container_name}$)" ]; then
                if [ "$(docker ps -aq -f status=exited -f name=^${ollama_container_name}$)" ]; then
                    echo "Starting existing Ollama container..."
                    docker start "$ollama_container_name"
                else
                    echo "Running new Ollama container ($ollama_image) and bind host Ollama models to container and vice verse..."
                    docker run -d "${docker_run_args[@]}" "$ollama_image"
                fi
                echo "Ollama container is now running on http://localhost:${ollama_host_port}"
            else
                echo "Ollama container is already running on http://localhost:${ollama_host_port}."
            fi
            
            echo "Ollama pulls model - ${ollama_model}."
            docker exec -it ollama ollama pull ${ollama_model}
        fi

        . ./../scripts/includes/call_container_shell.bash

    fi
fi




# # # EXAMPLES HOW TO RUN ROBOT IN CONTAINER
#
# # this section of code does not executing and just README
if [ ]; then # [ ] - same as false

### Build and run container (building can take long time and about 20 gb space)
bash run_basic.bash

### Commands inside container

## source ros workspace
. ~/.profile

## run gazebo with model (firstly)
ros2 launch d22diffw3_v2rc gazebo.launch.py

## Rebuild ROS2 package (only if you change something in packege)
colcon build --packages-select d22diffw3_v2rc 

## source ros workspace (after ros package rebuild)
. ~/.profile

fi

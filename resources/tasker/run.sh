#!/bin/bash

## runs Celery python file 'utecode/ut_tasker.py' as a python module. we
## wait a minute for the docker swarm services to finish standing up before 
## starting the Python / Celery program.
##
## the 'utecode' referenced below is a copy of the python code in this 
## project, built into the 'tasker' docker image before it was deployed 
## to the docker swarm. this bash script is also copied into that image 
## and placed in "/home/utbot/" and executed. Changes here will not be 
## reflected in execution until a new Docker tasker image is created and 
## deployed.
sleep 30

watchdog=0
while true; do
#  python3 -m utecode.tasker

  ## if watchdog is true, we've been here before and probably caught in a loop
#  if [[ $watchdog -eq 1 ]]; then
#    echo "Tasker died again! Staying dead..."
#    exit
#  else
#    echo "Tasker died!? Sleeping for 60 seconds and respawning"
#    watchdog=1
    sleep 60
#  fi
done


#!/bin/bash

## give docker-stack time to spin up services before connecting
echo "Waiting for Broker to start up."
echo "Sleeping 10 seconds"
sleep 10

## runs Celery python code as a worker, which will reach out and connect to
## the configured broker (Redis) service. the 'utecode' referenced below is a copy
## of the python code in this project, built into the worker docker image
## before it was deployed to the docker swarm. this bash script is also 
## copied into that image and placed in "/home/utbot/" and executed. Changes
## here will not be reflected in execution until a new Docker worker image
## is created and deployed.
##
## Since UTE runs in docker swarm nodes, a setting of '1' will 
## result in one Celery worker per Docker Swarm node. Multiple swarm nodes on
## one host will not be aware of each other. Find a balance between this and 
## the setting with the 'cpus: ' value in docker's "config/docker-stack.yml" 
## file for the worker image.

export LC_ALL=C.UTF-8
export LANG=C.UTF-8

rand=$(( ( RANDOM % 10000 ) + 1 ))
while true; do
  cd /home/utbot/utecode
  rqworker -u "redis://redis" --name "$rand@$UTE_HOSTNAME" \
    --path '/home/utbot/utecode' high medium low
  
  echo "Worker restarting?!?!?!"
  sleep 10
done

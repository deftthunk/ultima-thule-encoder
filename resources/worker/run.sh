#!/bin/bash

## give docker-compose time to spin up rabbitmq before celery 
## tries to connect
echo "Waiting for Broker to start up."
echo "Sleeping 45 seconds"
sleep 45

## runs Celery python code as a worker, which will reach out and connect to
## the configured broker (Redis) service. the 'utecode' referenced below is a copy
## of the python code in this project, built into the worker docker image
## before it was deployed to the docker swarm. this bash script is also 
## copied into that image and placed in "/home/utbot/" and executed. Changes
## here will not be reflected in execution until a new Docker worker image
## is created and deployed.
##
## '--time-limit' is how long a worker will
## allow a task to run before considering it a 'stuck' process and killing 
## the task. set to longer duration or remove if you plan on tasking very
## large chunks of work
##
## '--concurrency' determines how many celery workers to spawn for a given
## host. if removed, Celery will default to one worker per logical
## CPU core. 
##
## Since UTE runs in docker swarm nodes, a setting of '1' will 
## result in one Celery worker per Docker Swarm node. Multiple swarm nodes on
## one host will not be aware of each other. Find a balance between this and 
## the setting with the 'cpus: ' value in docker's "config/docker-stack.yml" 
## file for the worker image.
while true; do
  celery -A utecode worker --time-limit=3600 --loglevel=info --concurrency=1
  sleep 10
done

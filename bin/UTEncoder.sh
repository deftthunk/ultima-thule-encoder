#!/bin/bash

stackName='utestack'
script=`realpath $0`
scriptPath=`dirname $script`


## bring up docker containers, networking, and volumes as described in
## docker-stack.yml, deploy it as a "stack" to the swarm, and name the 
## stack instance 'utestack'
if [[ -z $1 ]] || [[ $1 == 'up' ]]; then
  docker stack deploy --compose-file $scriptPath/../config/docker-stack.yml $stackName

elif [[ $1 == 'down' ]]; then
  docker stack rm $stackName
  sleep 20
  docker container prune -f
  docker volume rm "$stackName"_nfs-in
  docker volume rm "$stackName"_nfs-out
fi



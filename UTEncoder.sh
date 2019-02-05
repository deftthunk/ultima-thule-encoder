#!/bin/bash

x265_path="./resources/x265/bin"
stackName='utestack'

## check for presence of built x265 tool, and kickoff compile process
## if not found.
if [[ ! -d $x265_path ]]; then
  origDir=$(pwd)
  parentDir=`dirname $x265_path`
  
  cd $parentDir
  `./runme.sh`
  cd $origDir
fi

if [[ -z $1 ]] || [[ $1 == 'up' ]]; then
  ## bring up docker containers, networking, and volumes as described in
  ## docker-stack.yml, deploy it as a "stack" to the swarm, and name the 
  ## stack instance 'utestack'
  docker stack deploy --compose-file ./config/docker-stack.yml $stackName

elif [[ $1 == 'down' ]]; then
  docker stack rm $stackName
  docker volume rm "$stackName"_nfs-in
  docker volume rm "$stackName"_nfs-out
fi



#!/bin/bash

x265_path="./resources/x265/bin"

## check for presence of built x265 tool, and kickoff compile process
## if not found.
if [[ ! -d $x265_path ]]; then
  parentDir=`dirname $x265_path`
  `$parentDir/runme.sh`
fi


## bring up docker containers, networking, and volumes as described in
## docker-compse.yml, deploy it as a "stack" to the swarm, and name the 
## stack instance 'utestack'
docker stack deploy --compose-file ./docker-compose.yml utestack

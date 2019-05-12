#!/bin/bash

script=`realpath $0`
scriptPath=`dirname $script`
uteRootPath="$scriptPath/../../.."

## print usage help
if [[ -z $1 ]]; then
  echo "> Usage:"
  echo "> build-img.sh <image name>"
  echo "> Images: tasker, worker, redis, x265-builder"
fi


if [[ $1 ]]; then
  oldId=`docker image ls -q ute:$1`
  docker build -f $uteRootPath/config/dockerfiles/Dockerfile.$1 -t ute:$1 $uteRootPath
  sleep 1
  echo "> Deleting old image: $oldId"
  docker rmi $oldId
fi



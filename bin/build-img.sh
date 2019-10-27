#!/bin/bash

## Builds Docker images for UTE using the Dockerfiles found in 'config/dockerfiles'.
## Can pass it an argument for a single container, or 'all' to build everything, which
## is useful for installation or resetting things to default.


script=`realpath $0`
scriptPath=`dirname $script`
uteRootPath="$scriptPath/.."

## print usage help
if [[ -z $1 ]]; then
  echo "> Usage:"
  echo "> build-img.sh <image name>"
  echo "> or "
  echo "> build-img.sh all"
  echo "> Images: tasker, worker, redis, x265-builder, aggregator, etc"
  exit 
fi


if [[ $1 == 'all' ]]; then
  for dockerfile in `ls -1 $uteRootPath/config/dockerfiles | cut -d '.' -f2`; do
    echo ">>"
    echo ">> Building Dockerfile.$dockerfile"
    echo ">>"

    sleep 1
    docker build -f $uteRootPath/config/dockerfiles/Dockerfile.$dockerfile \
      -t ute:$dockerfile $uteRootPath
  done

  sleep 1
  echo Pruning old images..
  docker image prune -f

elif [[ $1 ]]; then
  oldId=`docker image ls -q ute:$1`
  sleep 1
  docker build -f $uteRootPath/config/dockerfiles/Dockerfile.$1 -t ute:$1 $uteRootPath
  sleep 1
  echo "> Deleting old image: $oldId"
  docker rmi $oldId
fi

echo "Build Complete"

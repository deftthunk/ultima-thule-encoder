#!/bin/bash

##
## Ultima Thule Encoder Shell
##


## check if the docker image 'ute:shell' is present in the host's
## docker library. if not, build and tag it.
installTest=`docker image ls -q ute:shell`

if [[ -z $installTest ]]; then
  echo "> Image 'ute:shell' not found"
  echo "> Building image. This may take a minute..."

  imageId=`docker build -q -f dockerfiles/Dockerfile.shell .`
  docker image tag $imageId ute:shell
  
  echo "> ...done."
fi

## attempt to find the actual path of this script, so we can be sure
## we're executing in the top folder of UTE no matter how we are linked
## or executed. If the 'realpath' command isn't found, resort to more
## esoteric means
scriptPath=''
realpathPath=`whereis -b realpath | cut -d ':' -f2`

if [[ ! -f $realpathPath ]]; then
  scriptPath="$( cd "$(dirname "$0")" ; pwd -P )"
fi

script=`realpath $0`
scriptPath=`dirname $script`


## check if ansible/ansible.cfg file has been modified. if so, rebuild
## shell image and copy new ansible.cfg file into /root/.ansible.cfg
#ansibleCfgHash=`sha1sum ansible/ansible.cfg | cut -d ' ' -f1`
#if [[ $ansibleCfgHash != '0f1737acc32f17be2554840d56c09cf26d4f74ea' ]]; then
#  docker rmi ute:shell
#fi


## get user's uid and gid, so that the container can run with the same
## permissions and create/modify files with same permissions
#dockerUid=$(id -u)
#dockerGid=$(id -g)


## run shell container in interactive mode. we're mounting the top level
## directory of UTE, and also binding to this host's docker socket so we
## can administrate from within this container
docker run --rm -it \
  --mount type=bind,source=$scriptPath,target=/host \
  --mount type=bind,source='/var/run/docker.sock',target='/var/run/docker.sock' \
  ute:shell

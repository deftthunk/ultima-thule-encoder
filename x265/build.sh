#!/bin/bash

apt-get update
apt-get install -y --no-install-recommends mercurial cmake cmake-curses-gui build-essential gcc-arm-linux-gnueabi g++-arm-linux-gnueabi ca-certificates
mkdir src
cd src
hg clone https://bitbucket.org/multicoreware/x265
cd ./x265/build/linux
cmake -G "Unix Makefiles" "../../source"
#./make-Makefiles.bash &
make
cd ../../../..
cp ./src/x265/build/linux/x265 .
rm -rf ./src

#!/bin/bash

## grab everything needed for the build environment
apt-get update
apt-get install -y --no-install-recommends mercurial cmake cmake-curses-gui build-essential gcc-arm-linux-gnueabi g++-arm-linux-gnueabi ca-certificates

## go to directory where script is located
parentDir="$(dirname "$0")"
cd $parentDir
mkdir src
cd src

## download and compile x265 source
hg clone https://bitbucket.org/multicoreware/x265
cd ./x265/build/linux
cmake -G "Unix Makefiles" "../../source"
make

## copy the binary to our mount dir, and exit
cd $parentDir
cp ./src/x265/build/linux/x265 .
rm -rf ./src
chmod 777 x265

exit 0

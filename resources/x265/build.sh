#!/bin/bash

## grab everything needed for the build environment
apt-get update
apt-get install -y --no-install-recommends mercurial cmake cmake-curses-gui build-essential ca-certificates nasm

## go to directory where script is located
parentDir="$(dirname "$0")"
cd $parentDir
mkdir src
cd src

## download and compile x265 source
echo "Downloading x265 source code"
hg clone --config ui.clonebundles=false https://bitbucket.org/multicoreware/x265
cd ./x265/build/linux
cmake -G "Unix Makefiles" "../../source"
make

## copy the binary and libs to our mount dir, and exit
cd $parentDir
mkdir bin
cp ./src/x265/build/linux/x265 ./bin
cp ./src/x265/build/linux/libx265.so* ./bin

rm -rf ./src
chmod 777 -R ./bin

exit 0

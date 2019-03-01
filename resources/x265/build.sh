#!/bin/bash

sleep 5

## check to see if x265 already exists, and shutdown if so
if [[ -x '/ute/x265/x265' ]]; then
  exit 0
fi

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
cp ./src/x265/build/linux/x265 /ute/x265
cp ./src/x265/build/linux/libx265.so.* /ute/x265

chmod 777 -R /ute/x265

## shutdown
exit 0

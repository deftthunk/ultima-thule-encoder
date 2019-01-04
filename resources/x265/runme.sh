#!/bin/bash

docker run --rm --mount type=bind,source="$(pwd)",target=/target debian:stretch "/target/build.sh"

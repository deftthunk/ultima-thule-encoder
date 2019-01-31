#!/bin/bash

docker run --rm --mount type=bind,source="$(pwd)",target=/target debian:testing-slim "/target/build.sh"

#!/bin/bash

x265_path="./resources/x265/x265"

## check for presence of built x265 tool, and kickoff compile process
## if not found.
if [[ ! -f $x265_path ]]; then
  ./resources/x265_path/runme.sh
fi


docker stack deploy --compose-file ./docker-compose.yml utestack

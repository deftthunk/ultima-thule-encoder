#!/bin/bash

## wait for x265 to be built (if this is the first time running)
while true; do
  if [[ ! -x /ute/x265/x265 ]]; then
    sleep 5
  else
    break
  fi
done

ldconfig
exec gosu utbot /home/utbot/run.sh

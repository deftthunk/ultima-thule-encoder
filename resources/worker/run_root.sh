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
echo "executing..."
chmod 555 /home/utbot/run.sh
chmod -R 777 /home/utbot/utecode
exec gosu utbot /home/utbot/run.sh

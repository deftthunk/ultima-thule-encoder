#!/bin/bash

x265_path="./resources/x265/x265"

if [[ ! -f x265_path ]]; then
  ./resources/x265_path/runme.sh
fi

docker-compose -p ute up --build --scale celery_worker=2

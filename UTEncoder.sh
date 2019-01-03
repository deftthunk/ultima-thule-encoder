#!/bin/bash

x265_path="./x265/x265"

if [[ ! -f x265_path ]]; then
  ./x265_path/runme.sh
fi

docker-compose -p ute up --build --scale celery_worker=2

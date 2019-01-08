#!/bin/bash

# give docker-compose time to spin up rabbitmq before celery 
# tries to connect
sleep 45

while true; do
  celery -A ut_project worker --loglevel=info
  sleep 60
done

#!/bin/bash

# give docker-compose time to spin up rabbitmq before celery 
# tries to connect
echo "Waiting for RabbitMQ to start up."
echo "Sleeping 45 seconds"
sleep 45

while true; do
  celery -A ut_project worker --time-limit=600 --loglevel=info --concurrency=1
  sleep 30
done

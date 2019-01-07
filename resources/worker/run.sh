#!/bin/bash

sleep 5
while true; do
  celery -A code worker --loglevel=info
done

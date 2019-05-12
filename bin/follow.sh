#!/bin/bash

taskerId=$(docker ps -q --filter name='utestack_tasker*')
docker logs -f $taskerId

#!/bin/bash

taskerId=$(docker ps -q --filter name='utestack_red*')
docker logs -f $taskerId

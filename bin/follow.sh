#!/bin/bash

aggregatorId=$(docker ps -q --filter name='utestack_aggregator*')
docker logs -f $aggregatorId

#!/bin/bash

## setup for rabbitmq server on a docker container
## must run as root (or whichever user can exec rabbitmqctl)
##
## script must have 'rabbitmq-server' as last execute, but all
## 'rabbitmqctl' commands must run once the server is up. so, we
## fork them off behind a sleep, and they run once the server is up

sleep 20 && \
# enable rabbitmq management plugin for flower access
rabbitmq-plugins enable rabbitmq_management && \
# set TTL policy
rabbitmqctl set_policy TTL ".*" '{"message-ttl":600000}' --apply-to queues && \
# add user 'utbot' with password 'ultimaThule'
rabbitmqctl add_user utbot ultimaThule && \
# add virtual host 'utbot_vhost'
rabbitmqctl add_vhost utbot_vhost && \
# add user tag 'utbot_tag' for user 'utbot'
rabbitmqctl set_user_tags utbot utbot_tag administrator && \
# set permissions for user 'utbot' on virtual host 'utbot_vhost'
rabbitmqctl set_permissions -p utbot_vhost utbot ".*" ".*" ".*" &


# start rabbitmq server instance
rabbitmq-server

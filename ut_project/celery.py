from __future__ import absolute_import
from celery import Celery

app = Celery('ut_project', 
        broker='amqp://utbot:ultimaThule@ute_rabbitmq/utbot_vhost',
        backend='rpc://',
        include=['ut_project.tasks'])


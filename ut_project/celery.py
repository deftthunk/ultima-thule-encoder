from __future__ import absolute_import
from celery import Celery

app = Celery('ut_project', 
        broker='amqp://utbot:ultimaThule@rabbitmq/utbot_vhost',
        backend='rpc://',
        include=['ut_project.tasks'])


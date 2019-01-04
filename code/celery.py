from __future__ import absolute_import
from celery import Celery

app = Celery('ut_encode', 
        broker='amqp://utbot:ultimaThule@172.18.100.2/utbot_vhost',
        backend='rpc://',
        include=['ut_encode.tasks'])


from __future__ import absolute_import
from code import Celery

app = Celery('code', 
        broker='amqp://utbot:ultimaThule@172.18.100.2/utbot_vhost',
        backend='rpc://',
        include=['code.tasks'])


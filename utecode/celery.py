from celery import Celery

app = Celery('utecode', 
        broker='amqp://utbot:ultimaThule@rabbitmq/utbot_vhost',
        backend='rpc://',
        include=['utecode.tasks'])


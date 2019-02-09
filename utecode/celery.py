from celery import Celery

app = Celery('utecode')

## Celery config settings affecting the broker, tasker, and workers
## http://docs.celeryproject.org/en/latest/userguide/configuration.html
app.conf.update(
        broker_url='redis://redis:6379/0',
        result_backend='redis://redis:6379/0',
        broker_heartbeat=60,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        include=['utecode.tasks']
)


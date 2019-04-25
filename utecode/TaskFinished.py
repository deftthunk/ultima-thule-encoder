from celery.exceptions import CeleryError

class TaskFinished(CeleryError):
    def __str__(self):
        return self.message

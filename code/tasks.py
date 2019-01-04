from __future__ import absolute_import
from ut_encode.celery import app
import time

@app.task
def longtime_add(x, y):
    print('long time task begins')
    time.sleep(5)
    print('long time task finished')
    return x + y

@app.task
def subtract(x, y):
    print('start subtract')
    time.sleep(5)
    print('subtract finished')
    return x - y

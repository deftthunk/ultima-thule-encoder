from utecode.celery import app
from time import sleep
import subprocess

@app.task
def encode(cmd):
    status = subprocess.run(cmd, shell=True, stderr=subprocess.STDOUT, \
            stdout=subprocess.PIPE)

    return (status.returncode, status.stdout)
        
@app.task
def testMe(arg):
    sleep(10)

    return arg

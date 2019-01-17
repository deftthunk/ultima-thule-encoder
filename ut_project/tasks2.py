from ut_project.celery import app
from time import sleep

@app.task
def encode(cmd):
    status = subprocess.run(cmd, shell=True, stderr=subprocess.STDOUT, \
            stdout=subprocess.PIPE)

    return (status.returncode, status.stdout)
        



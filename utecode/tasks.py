from utecode.celery import app
from time import sleep
import re
import logging
import subprocess

#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.basicConfig(level=logging.DEBUG)


@app.task
def encode(cmd):
    status = subprocess.run(cmd, shell=True, stderr=subprocess.STDOUT, \
            stdout=subprocess.PIPE)

    logging.debug("status.stdout: " + str(status.stdout))
    fps = parseFPS(status.stdout)

    logging.info("Status: " + str(status.returncode))
    return fps


## find the last printout of x265's 'frames per second' estimate, which ought
## to be the average processing speed of the node its running on
def parseFPS(string):
    fps = re.findall(r'\s(\d+\.\d+)\sfps', string.decode('utf-8'))
    logging.debug("fps size: " + str(len(fps)))
    
    return fps[-1]

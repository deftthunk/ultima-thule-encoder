from time import sleep
import re
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

## take a CLI command from the task and execute it via subprocess module
## the decorator '@app.task' enables retrying for failed tasks which
## will be seen in Flower
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

    ## debugging stuff
    logging.debug("cmd output: " + str(string.decode('utf-8')))
    logging.debug("fps size: " + str(len(fps)))
    
    if len(fps) > 0:
        return fps[-1]
    else:
        return 00
    

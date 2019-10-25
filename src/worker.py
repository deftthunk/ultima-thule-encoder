from time import sleep
from platform import node
import re, os
import logging
import subprocess

rootLogger = logging.getLogger('ute')
rootLogger.setLevel(logLevel)
socketHandler = logging.handlers.SocketHandler('aggregator', 
      logging.handlers.DEFAULT_TCP_LOGGING_PORT)

rootLogger.addHandler(socketHandler)
workerLogger = logging.getLogger("ute.worker")


def encode(cmd):
    workerLogger.debug("Starting Popen")
    status = subprocess.run(cmd, shell=True, stderr=subprocess.STDOUT, \
        stdout=subprocess.PIPE)

    #logging.debug("CMD STDOUT:: >> " + str(status.stdout))
    workerLogger.info("Status: " + str(status.returncode))
    fps = parseFPS(status.stdout)
    workerLogger.debug("====================================")

    return (fps, hostname(), nodename())


'''
find the last printout of x265's 'frames per second' estimate, which ought
to be the average processing speed of the node its running on
'''
def parseFPS(string):
    fps = re.findall(r'\s(\d+\.\d+)\sfps', string.decode('utf-8'))

    ## debugging stuff
    #logging.debug("fps output:: >> " + str(string.decode('utf-8')))
    workerLogger.debug("fps size:: >>" + str(len(fps)))
    
    if len(fps) > 0:
        return fps[-1]
    else:
        logging.debug("fps return fail!!!")
        return 00


## get UTE assigned hostname (from Dockerfile)
def hostname():
    name = os.environ['UTE_HOSTNAME']
    return name.rstrip()

## get docker swarm assigned hostname
def nodename():
    clientId = node()
    return clientId.rstrip()


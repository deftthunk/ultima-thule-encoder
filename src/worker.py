import re, os
import logging.handlers
import subprocess
from time import sleep
from platform import node
from typing import Tuple
from logging import getLogger, debug, INFO

rootLogger = getLogger('ute')
rootLogger.setLevel(INFO)
socketHandler = logging.handlers.SocketHandler('aggregator', 
    logging.handlers.DEFAULT_TCP_LOGGING_PORT)

rootLogger.addHandler(socketHandler)
workerLogger = getLogger("ute.worker")


def encode(cmd: str) -> Tuple[float, str, str]:
    workerLogger.debug(str(cmd))
    workerLogger.debug("Starting Popen")
    status = subprocess.run(cmd, shell=True, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)

    #workerLogger.debug("CMD STDOUT:: >> " + str(status.stdout))
    workerLogger.debug("Status: " + str(status.returncode))
    fps = parseFPS(status.stdout)
    host = hostname()
    node = nodename()
    workerLogger.debug("====================================")
    workerLogger.debug("fps: " + str(fps))

    ## give container a chance to write everything out to network drive. otherwise
    ## it can give RQ problems and hang
    jobPath = re.search(r' -o .*?(/.*\.265)', str(cmd))
    flush = subprocess.run(["sync", "-f", jobPath.group(1)], close_fds=True)

    return (fps, host, node)


'''
find the last printout of x265's 'frames per second' estimate, which ought
to be the average processing speed of the node its running on
'''
def parseFPS(string: str) -> float:
    fps = re.findall(r'\s(\d+\.\d+)\sfps', string.decode('utf-8'))

    ## debugging stuff
    #logging.debug("fps output:: >> " + str(string.decode('utf-8')))
    #workerLogger.debug("fps size:: >>" + str(len(fps)))
    
    if len(fps) > 0:
        return fps[-1]
    else:
        workerLogger.debug("fps return fail!!!")
        return 0.0


## get UTE assigned hostname (from Dockerfile)
def hostname() -> str:
    name = os.environ['UTE_HOSTNAME']
    return name.rstrip()

## get docker swarm assigned hostname
def nodename() -> str:
    clientId = node()
    return clientId.rstrip()


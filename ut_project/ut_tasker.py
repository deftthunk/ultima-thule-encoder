from .tasks2 import *
import time, re
import subprocess


'''
TODO: 
- have variables defined by users in a config file, not here.
- add asyncio functionality for monitoring when celery worker count changes


clientCheckFreq -- how often to check for changes in number of workers
frameBufferCount -- number of frames on either side of 'jobSize' to help encoder
jobSize -- number of frames per task, rounded up +1 to ensure full coverage
jobCount -- number of task blocks to create for distributing to worker nodes
'''
clientCheckFreq = 60
frameBufferCount = 100
jobSize = 0
jobCount = 0


## ping Celery clients using the broker container to get a count of 
## available workers in the swarm
def getClientCount():
    pingRet = app.control.ping(timeout=1.0)

    return len(ping)


## use the installed tool 'mediainfo' to determine number of frames
def getFrameCount(target):
    cmdRet = subprocess.run(["mediainfo", "--fullscan", target], stdout=subprocess.PIPE)
    match = re.match('^Frame count\s+\:\s(\d+)', cmdRet.stdout)

    return match.group(1)


## use installed tool 'ffmpeg' to detect video cropping in target sample
def detectCropping(target):
    cmdRet = subprocess.run(["ffmpeg", "-ss", "900", "-i", target, "-t", "1", \
            "-vf", "cropdetect=24:16:0", "-preset", "ultrafast", "-f", "null", \
            "-"], stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    match = re.search(r'\s(crop=\d+\:\d+[^a-zA-Z]*?)\n', cmdRet.stdout.decode('utf-8'))
    
    if match:
        return match.group(1)
    else:
        return ''


def buildCmdString():
    jobSize = (getFrameCount() / jobCount) + 1



def main():
    








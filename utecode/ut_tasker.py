from .tasks import *
from .timeout import timeout
import time, re, os
import subprocess
import logging
import json


'''
TODO: 
- have variables defined by users in a config file, not here.
- add asyncio functionality for monitoring when celery worker count changes
- provide Celery worker direct and broadcast control for monitoring
'''
workFolder = "/inbound"
outFolder = "/outbound"

#logging.basicConfig(filename='/utelogs/tasker.log', level=logging.WARN, \
#                    format='%(asctime)s %(message)s')
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')


if __name__ == "__main__":
    main()


## delete all pending tasks in the Broker queue
def purgeTasks():
    numDeleted = app.control.purge()

    logging.info("Tasks purged: " + str(numDeleted))
    return numDeleted


## grab first file found in the NFS 'inbound' folder
def findWork():
    listOfFiles = list()
    for (dirPath, dirNames, fileNames) in os.walk(workFolder):
        listOfFiles += [os.path.join(dirPath, entry) for entry in fileNames]

    logging.info("Files found: " + str(len(listOfFiles)))
    return listOfFiles


## ping Celery clients using the broker container to get a count of 
## available workers in the swarm
def getClientCount():
    pingRet = None

    while True:
        try:
            pingRet = app.control.ping(timeout=3.0)
        except:
            sleep(1)
            continue
        break

    logging.info("Celery Clients: " + str(len(pingRet)))
    return len(pingRet)


## use 'mediainfo' tool to determine number of frames
def getFrameCount(target):
    cmdRet = subprocess.run(["mediainfo", "--fullscan", target], \
            stdout=subprocess.PIPE)
    match = re.search('Frame count.*?(\d+)', cmdRet.stdout.decode('utf-8'))

    logging.info("Frame Count: " + match.group(1))
    return int(match.group(1))


## use ffmpeg to detect video cropping in target sample
def detectCropping(target):
    cmdRet = subprocess.run(["ffmpeg", "-ss", "900", "-i", target, "-t", "1", \
            "-vf", "cropdetect=24:16:0", "-preset", "ultrafast", "-f", "null", \
            "-"], stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    match = re.search(r'\s(crop=\d+\:\d+[^a-zA-Z]*?)\n', cmdRet.stdout.decode( \
            'utf-8'))
    
    if match:
        logging.info("Cropping: " + match.group(1))
        return match.group(1)
    else:
        logging.info("Cropping: n/a")
        return ''


'''
Build the ffmpeg/x265 command line parameters, filling in relevant variables
for user-defined encode settings, file location, output naming scheme, etc. 
Each completed string is pushed onto an array for later delivery to RabbitMQ 
as a task for Celery workers.

<< ffmpeg / x265 argument variables >>

encodeTasks -- list (array) storing built ffmpeg commands
frameBufferSize -- frames on either side of 'jobSize' to help prime encoder
jobCount -- number of task blocks to create for distributing to worker nodes
crop -- if cropping is present, add flag to ffmpeg. else leave blank string
jobSize -- number of frames per task, rounded up +1 to ensure full coverage
counter -- track progress into jobCount
seek -- position marker in target file for chunks
chunkStart / chunkEnd -- block of frames that define a 'job'
frames -- number of frames to encode between two 'frameBufferSize' amounts
'''
def buildCmdString(target, frameCountTotal, clientCount):
    encodeTasks = []
    frameBufferSize = 100
    jobCount = clientCount * 1200
    jobSize = int(round(frameCountTotal / jobCount) + 1)
    crop = detectCropping(target)
    if crop != '':
        tempCrop = crop
        crop = "-filter:v \"{}\"".format(tempCrop)

    ## initial values for first loop iteration
    counter, seek, chunkStart = 0, 0, 0
    chunkEnd = jobSize - 1
    frames = jobSize + frameBufferSize

    ## ffmpeg and x265 CLI args, with placeholder variables defined in the 
    ## .format() method below
    while counter < jobCount:
        ffmpegStr = "ffmpeg \
                -hide_banner \
                -loglevel fatal \
                -i {tr} \
                {cd} \
                -strict \
                -1 \
                -f yuv4mpegpipe - | x265 - \
                --log-level error \
                --no-progress \
                --no-open-gop \
                --seek {sk} \
                --frames {fr} \
                --chunk-start {cs} \
                --chunk-end {ce} \
                --colorprim bt709 \
                --transfer bt709 \
                --colormatrix bt709 \
                --crf=20 \
                --fps 24000/1001 \
                --min-keyint 24 \
                --keyint 240 \
                --sar 1:1 \
                --preset slow \
                --ctu 16 \
                --y4m \
                --pools \"+\" \
                -o {dst}/chunk{ctr}.265".format( \
                tr = target, \
                cd = crop, \
                sk = seek, \
                fr = frames, \
                cs = chunkStart, \
                ce = chunkEnd, \
                ctr = counter, \
                dst = outFolder)

        ## push built CLI command onto end of list
        encodeTasks.append(ffmpegStr)

        chunkStart = frameBufferSize
        chunkEnd = chunkStart + jobSize - 1
        frames = jobSize + (frameBufferSize * 2)
        seek = seek + frames - (frameBufferSize * 2)
        counter += 1

    logging.info("Encode Tasks: " + str(len(encodeTasks)))
    return encodeTasks


'''
Queue up all tasks by calling 'encode.delay()', which is a Celery method for 
asynchronously queuing tasks in our message broker (RabbitMQ). 'encode' is 
referencing the custom function each Celery worker is carrying which excutes 
the ffmpeg task.

'encode.delay()' returns a handle to that task with methods for determing the 
state of the task. Handles are stored in 'statusHandles' list for later use.
'''
def populateQueue(encodeTasks):
    statusHandles = []
    taskId = 0

    for task in encodeTasks:
        statusHandles.append(encode.delay(task, taskId))
        taskId += 1
 
    logging.info("Tasks queued: " + str(len(statusHandles)))
    return statusHandles


'''
Once the queue in RabbitMQ is populated with encoding tasks, use Celery 
to watch for when tasks are passed to a worker and become 'active'. Once 
active, we can get that list of active tasks and periodically poll their
status for results.

When a task is finished, we check the result, and if sucessful, discard 
it and refresh our active task list. If unsucessful, attempt to resubmit 
the task to the queue.
'''
def waitForTaskCompletion(taskHandles):
    ## find all celery worker id's
    workerIds = None
    while True:
        try:
            workerIds = app.control.inspect()
        except:
            sleep(1)
            continue
        break

    for worker in workerIds:
        logging.debug("Worker: " + worker)

        ## wait on tasks
        for handle in taskHandles:
            workerDict = handle.active()
        
            if handle.ready():
                for worker,task in workerDict:
                    logging.debug("Worker: " + worker)
                    logging.debug("Task ID: " + task[0]['id'])



@timeout(15)
def testPopulateQueue(target):
    taskHandles = []
    counter = 0
    
    while counter < 100:
        cmdString = "sleep 30"
        taskHandles.append(encode.delay(cmdString))
        counter += 1

    return taskHandles


def main():
    while True:
        files = findWork()

        ## check for files and clients
        if len(files) == 0:
            logging.debug("No files, sleeping")
            sleep(10)
            continue
        if getClientCount() == 0:
            logging.debug("No clients, sleeping")
            sleep(10)
            continue

        ## work through files found
        for targetFile in files:
            ## check if client list has changed
            clientCount = getClientCount()
            if clientCount == 0:
                logging.debug("No clients, sleeping")
                sleep(10)
                break

            frameCountTotal = getFrameCount(targetFile)
            encodeTasks = buildCmdString(files[0], frameCountTotal, clientCount)
            taskHandles = testPopulateQueue(encodeTasks)
            waitForTaskCompletion(taskHandles)


            logging.debug("encodeTasks: ", str(len(encodeTasks)))
            break




'''
>>> i.active()
>>> i = app.control.inspect()
>>> i.active()
{'celery@887bb6b3ba9b': [{'args': '(2,)', 'delivery_info': {'routing_key': 'celery', 'exchange': '', 'redelivered': False, 'priority': 0}, 'hostname': 'celery@887bb6b3ba9b', 'id': 'e0893927-1a30-455b-b73c-b2d01b3ac675', 'kwargs': '{}', 'worker_pid': 14, 'time_start': 1548127895.8416011, 'name': 'utecode.tasks.testMe', 'type': 'utecode.tasks.testMe', 'acknowledged': True}], 'celery@71d29c97cead': [], 'celery@a7f5c631d6d7': [], 'celery@c4ffd6300b59': []}
>>> i.active()
{'celery@887bb6b3ba9b': [], 'celery@71d29c97cead': [], 'celery@a7f5c631d6d7': [], 'celery@c4ffd6300b59': []}


'''

'''
>>> ret = testMe.delay(2)
>>> ret.ready()
True
>>> ret.result()
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
TypeError: 'int' object is not callable
>>>
>>>
>>> ret.result  
2
'''

from .tasks import *
from .timeout import timeout
from celery.result import ResultSet
import time, re, os, math
import subprocess
import logging


'''
TODO: 
- have variables defined by users in a config file, not here.
- add asyncio functionality for monitoring when celery worker count changes
- provide Celery worker direct and broadcast control for monitoring
'''
workFolder = "/inbound"
outFolder = "/outbound"

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')


if __name__ == "__main__":
    main()


## delete all pending tasks in the Broker queue
@timeout(60)
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


## find the number of frames in the video. this can be error prone, so multiple
## methods are attempted
def getFrameCount(target):
    frameCount = None

    ## attempting mediainfo method
    mediaRet = subprocess.run(["mediainfo", "--fullscan", target], \
            stdout=subprocess.PIPE)
    mediaMatch = re.search('Frame count.*?(\d+)', mediaRet.stdout.decode('utf-8'))

    ## if we cant find a frame count, we'll do it the hard way and count frames
    ## using ffprobe. this can end up being the case if the MKV stream is
    ## variable frame rate
    if mediaMatch == None:
        logging.info("Using ffprobe")
        ffprobeRet = subprocess.run(["ffprobe", \
            "-v", \
            "error", \
            "-count_frames", \
            "-select_streams", \
            "v:0", \
            "-show_entries", \
            "stream=nb_read_frames", \
            "-of", \
            "default=nokey=1:noprint_wrappers=1", \
            target], stdout=subprocess.PIPE)

        frameCount = ffprobeRet.stdout.decode('utf-8')
    else:
        frameCount = mediaMatch.group(1)

    logging.info("Frame Count: " + str(frameCount))
    return int(frameCount)


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
    jobCount = 100
    jobSize = int(math.ceil(frameCountTotal / jobCount))

    crop = detectCropping(target)
    if crop != '':
        tempCrop = crop
        crop = "-filter:v \"{}\"".format(tempCrop)

    ## initial values for first loop iteration
    counter, seek, chunkStart = 0, 0, 0
    chunkEnd = jobSize - 1
    frames = jobSize + frameBufferSize

    logging.debug("jobCount / jobSize / frameCountTotal: " + str(jobCount) + "/" + \
            str(jobSize) + "/" + \
            str(frameCountTotal))

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
        ## if debugging, cut out excess spaces from command string
        logging.debug(' '.join(ffmpegStr.split()))

        chunkStart = frameBufferSize
        if counter == 0:
          seek = jobSize - chunkStart
        else:
          seek = seek + jobSize

        ## if we're about to encode past EOF, set chunkEnd to finish on the 
        ## last frame, and adjust 'frames' accordingly. else, continue
        if (seek + (frameBufferSize * 2) + jobSize) > frameCountTotal:
          chunkEnd = frameCountTotal - seek
          frames = chunkEnd
        else:
          chunkEnd = chunkStart + jobSize - 1
          frames = jobSize + (frameBufferSize * 2)

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
@timeout(300)
def populateQueue(encodeTasks):
    r = ResultSet([])
    taskHandles = {}

    for task in encodeTasks:
        ret = encode.delay(task)
        r.add(ret)
        taskHandles[ret.task_id] = ret
 
    logging.info("Tasks queued: " + str(len(taskHandles)))
    return taskHandles, r


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
            workerTaskingDict = app.control.inspect().active()
            if len(workerTaskingDict) < 1:
                continue
        except:
            sleep(1)
            continue
        break

    '''
    while dict 'taskHandles' has items, loop over every Celery worker and every
    task/thread each worker has (by default 1 per worker). grab the task ID of
    the current task that worker has, and find it in 'taskHandles' dict. Use
    the dict value to check the status of the task with the object's ".ready()"
    method. When true, delete task entry from dict.
    
    layout of workerTaskingDict:
    {
        'celery@caff7e415107': [], 
        'celery@87eaf40ea151': [
        {
            'id': 'bb82ac8d-f32e-44f6-8051-e7d949ab9679', 
            'name': 'utecode.tasks.encode', 
            'args': "
            (
                'sleep 30',
            )", 
            'kwargs': '{}', 
            'type': 'utecode.tasks.encode', 
            'hostname': 'celery@87eaf40ea151', 
            'time_start': 1549599207.44921, 
            'acknowledged': True, 
            'delivery_info': 
            {
                'exchange': '', 
                'routing_key': 'celery', 
                'priority': 0, 
                'redelivered': None
            }, 
            'worker_pid': 11
        }]
    }
    
    ''' 
    taskList = []
    while len(taskHandles) > 0:
        workerTaskingDict = app.control.inspect().active()
        for worker in workerTaskingDict.keys():
            logging.debug("Worker: " + worker)
            ## wait on tasks
            for taskItem in workerTaskingDict[worker]:
                taskList.append(taskItem['id'])
        
        flag = True
        while flag:
            for task in taskList:
                if taskHandles[task].ready():
                    logging.info("Result: " + str(taskHandles[task].result))
                    del taskHandles[task]
                    taskList.remove(task)
                    flag = False
                    logging.info("Completed task " + task)
                    break

                sleep(2)



def testCallback(taskId, result):
    logging.info("TaskID: " + taskId)
    logging.info("Result: " + str(result))
    

def rebuildVideo():


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
            encodeTasks = buildCmdString(targetFile, frameCountTotal, clientCount)
            taskHandles, retSet = populateQueue(encodeTasks)
            retSet.join(callback=testCallback)
#            waitForTaskCompletion(taskHandles)


            break
        
        logging.info("\nFinished " + files[0])
        sleep(10)
        break




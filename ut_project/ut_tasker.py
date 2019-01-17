from .tasks2 import *
import time, re
import subprocess


'''
TODO: 
- have variables defined by users in a config file, not here.
- add asyncio functionality for monitoring when celery worker count changes
- provide Celery worker direct and broadcast control for monitoring
'''

## ping Celery clients using the broker container to get a count of 
## available workers in the swarm
def getClientCount():
    pingRet = app.control.ping(timeout=5.0)

    return len(ping)


## use the installed tool 'mediainfo' to determine number of frames
def getFrameCount(target):
    cmdRet = subprocess.run(["mediainfo", "--fullscan", target], \
            stdout=subprocess.PIPE)
    match = re.match('^Frame count\s+\:\s(\d+)', cmdRet.stdout)

    return match.group(1)


## use installed tool 'ffmpeg' to detect video cropping in target sample
def detectCropping(target):
    cmdRet = subprocess.run(["ffmpeg", "-ss", "900", "-i", target, "-t", "1", \
            "-vf", "cropdetect=24:16:0", "-preset", "ultrafast", "-f", "null", \
            "-"], stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    match = re.search(r'\s(crop=\d+\:\d+[^a-zA-Z]*?)\n', cmdRet.stdout.decode( \
            'utf-8'))
    
    if match:
        return match.group(1)
    else:
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
    jobCount = clientCount * 5
    jobSize = (frameCountTotal / jobCount) + 1
    crop = detectCropping(target)
    if crop != '':
        tempCrop = crop
        crop = "-filter:v \"{}\"".format(tempCrop)

    ## initial values for first loop iteration
    counter, seek, chunkstart = 0, 0, 0
    chunkEnd = jobSize - 1
    frames = jobsize + frameBufferSize

    ## ffmpeg and x265 CLI args, with placeholder variables defined in the 
    ## .format() method below
    while counter < jobSize:
        ffmpegStr = "ffmpeg \
                -hide_banner \
                -i {tr} \
                {cd} \
                -strict \
                -1 \
                -f yuv4mpegpipe - | x265 - \
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
                --min \
                -keyint 24 \
                --keyint 240 \
                --sar 1:1 \
                --preset slow \
                --ctu 16 \
                --y4m \
                --pools \"+\" \
                -o chunk{ctr}.265".format( \
                tr = target, \
                cd = crop, \
                sk = seek, \
                fr = frames, \
                cs = chunkStart, \
                ce = chunkEnd, \
                ctr = "counter: ") \
        )

        ## push built CLI command onto end of list
        encodeTasks.append(ffmpegStr)

        chunkStart = frameBufferSize
        chunkEnd = chunkStart + jobSize - 1
        frames = jobSize + (frameBuffer * 2)
        seek = seek + frames - (frameBufferSize * 2)
        counter += 1


    return encodeTasks


'''
asdf
'''
def populateQueue(encodeTasks):
    taskId = 0

    for task in encodeTasks:
        encode.delay(taskId, task)


'''
Once the queue in RabbitMQ is populated with encoding tasks, use Celery 
to watch for when tasks are passed to a worker and become 'active'. Once 
active, we can get that list of active tasks and periodically poll their
status for results.

When a task is finished, we check the result, and if sucessful, discard 
it and refresh our active task list. If unsucessful, attempt to resubmit 
the task to the queue.
'''
def waitForTaskCompletion():
    i = app.control.inspect()

    #http://docs.celeryproject.org/en/latest/userguide/workers.html#dump-of-reserved-tasks




def main():
    frameCountTotal = getFrameCount()
    clientCount = getClientCount()
    
    encodeTasks = buildCmdString(target, frameCountTotal, clientCount)
    populateQueue(encodeTasks)





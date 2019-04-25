import re, os, math, sys
import subprocess
import logging
from time import sleep
from redis import Redis
from rq import Queue, Worker


workFolder = "/ute/inbound"
outFolder = "/ute/outbound"

#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.basicConfig(level=logging.DEBUG)

## delete all pending tasks in the Broker queue
def purgeQueue(q):
    #numDeleted = app.control.purge()
    q.empty()

    #logging.info("Queued tasks purged: " + str(numDeleted))
    logging.info("Queued jobs purged")
    
    #return numDeleted


## kill all active tasks
def purgeActive(q):
    for job in q.jobs:
        if job.get_status() == 'started':
            job.cancel

    logging.info("All tasks purged")


## grab first file found in the NFS 'inbound' folder
def findWork():
    listOfFiles = list()
    for (dirPath, dirNames, fileNames) in os.walk(workFolder):
        listOfFiles += [os.path.join(dirPath, entry) for entry in fileNames]

    logging.info("Files found: " + str(len(listOfFiles)))
    return listOfFiles


## find clients using the broker container to get a count of 
## available workers in the swarm
def getClientCount(redisLink):
    workers = Worker.all(connection=redisLink)
    logging.info("Found " + str(len(workers)) + " workers")

    for worker in workers:
        logging.info(str(worker.name))

    return len(workers)


## return the base name of a filepath. if no arg given, return the name of
## the current file
def getFileName(name=None):
    fileName = ''
    if name == None:
        logging.debug("No args, finding new work")
        currentFileList = findWork()
        if len(currentFileList) > 0:
            fileName = os.path.basename(currentFileList[0])
        else:
            logging.error("No files in queue")
    else:
        fileName = os.path.basename(name)

    return fileName


## find the number of frames in the video. this can be error prone, so multiple
## methods are attempted
def getFrameCount(target):
    frameCount, frameRate, duration = None, None, None

    ## attempting mediainfo method
    mediaRet = subprocess.run(["mediainfo", "--fullscan", target], \
            stdout=subprocess.PIPE)
    mediaMatch = re.search('Frame count.*?(\d+)', mediaRet.stdout.decode('utf-8'))
    mediaFrameRate = re.search('Frame rate.*?(\d\d\d?)(\.\d\d?\d?)?', \
            mediaRet.stdout.decode('utf-8'))
    mediaDuration = re.search('Duration.*?\s(\d{2,})\s*\n', \
            mediaRet.stdout.decode('utf-8'))

    ## get frame rate
    if mediaFrameRate.group(2):
        frameRate = mediaFrameRate.group(1) + mediaFrameRate.group(2)
    else:
        frameRate = mediaFrameRate.group(1)

    ## get time duration
    duration = mediaDuration.group(1)

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
    logging.info("Frame Rate: " + str(frameRate))
    logging.info("Duration: " + str(duration))
    logging.info("Calc FC: " + str(float(duration) * float(frameRate) / 1000))

    return int(frameCount), float(frameRate), int(duration)


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

fileString -- the name of the file chunk generated: <num>_chunk_<filename>.265
encodeTasks -- list (array) storing built ffmpeg commands
frameBufferSize -- frames on either side of 'jobSize' to help prime encoder
jobCount -- number of task blocks to create for distributing to worker nodes
crop -- if cropping is present, add flag to ffmpeg. else leave blank string
jobSize -- frames per task; uses ceil() to ensure last task gets full coverage
counter -- track progress into jobCount, and used to label file chunks
seek -- position marker in target file for chunks
chunkStart / chunkEnd -- block of frames that define a 'job'
frames -- number of total frames to encode. some will be used just prime encoder
'''
def buildCmdString(target, frameCountTotal, frameRate, duration, clientCount):
    encodeTasks = []
    fileString = ''
    frameBufferSize = 100
    jobSize = 100
    jobCount = int(math.ceil(frameCountTotal / jobSize))
    counter = 0

    ## handle two special cases that mess up encoding. Exit if either is true
    if jobSize < frameBufferSize:
        logging.error("Error: jobSize must be at least as large as frameBufferSize")
        sys.exit()
    if jobCount < 3:
        logging.error("Error: jobCount must be higher. Please decrease jobSize")
        sys.exit()

    ## build the output string for each chunk
    def genFileString():
        nonlocal fileString
        namePart = ''
        fileName = getFileName(target)
        newFolder = 'ute_' + fileName
        print("Newfolder " + newFolder)
        newPath = '/'.join([outFolder, newFolder])
        print("newPath: " + newPath)
        os.makedirs(newPath, 0o755, exist_ok=True)
        #logging.info("Outbound path: " + str(newPath))

        ## use part (or all) of the filename to help name chunks
        if len(fileName) > 10:
          namePart = fileName[0:9]
        else:
          namePart = fileName

        numLen = len(str(jobCount))

        ## prepend each name with a zero-padded number for ordering. pad
        ## the number with the amount of digit locations we need
        fileString = newFolder + '/'
        if numLen <= 3:
            fileString += ''.join("{:03d}".format(counter))
        elif numLen == 4:
            fileString += ''.join("{:04d}".format(counter))
        elif numLen == 5:
            fileString += ''.join("{:05d}".format(counter))
        elif numLen == 6:
            fileString += ''.join("{:06d}".format(counter))
        else:
            fileString += ''.join("{:07d}".format(counter))

        fileString += "_" + namePart + ".265"
        logging.debug("FileString: " + str(fileString))


    ## determine if cropping will be included
    crop = detectCropping(target)
    if crop != '':
        tempCrop = crop
        crop = "-filter:v \"{}\"".format(tempCrop)

    ## initial values for first loop iteration
    seek, seconds, chunkStart = 0, 0, 0
    chunkEnd = jobSize - 1
    frames = jobSize + frameBufferSize
    genFileString()

    logging.debug("jobCount / jobSize / frameCountTotal: " + str(jobCount) + "/" + \
            str(jobSize) + "/" + \
            str(frameCountTotal))

    ## ffmpeg and x265 CLI args, with placeholder variables defined in the 
    ## .format() method below
    while counter <= jobCount:
        ffmpegStr = "ffmpeg \
                -hide_banner \
                -loglevel fatal \
                -ss {sec} \
                -i {tr} \
                {cd} \
                -strict \
                -1 \
                -f yuv4mpegpipe - | x265 - \
                --log-level error \
                --no-open-gop \
                --frames {fr} \
                --chunk-start {cs} \
                --chunk-end {ce} \
                --colorprim bt709 \
                --transfer bt709 \
                --colormatrix bt709 \
                --crf=20 \
                --fps {frt} \
                --min-keyint 24 \
                --keyint 240 \
                --sar 1:1 \
                --preset slow \
                --ctu 16 \
                --y4m \
                --pools \"+\" \
                -o {dst}/{fStr}".format( \
                tr = target, \
                cd = crop, \
                fr = frames, \
                cs = chunkStart, \
                ce = chunkEnd, \
                ctr = counter, \
                frt = frameRate, \
                sec = seconds, \
                dst = outFolder, \
                fStr = fileString)

        ## push built CLI command onto end of list
        encodeTasks.append(ffmpegStr)
        ## if debugging, cut out excess spaces from command string
        logging.debug(' '.join(ffmpegStr.split()))

        chunkStart = frameBufferSize
        if counter == 0:
            seek = jobSize - chunkStart
            if seek < 0:
                seek = 0
            seconds = fileSeek(seek, frameRate)
        else:
            seek = seek + jobSize
            seconds = fileSeek(seek, frameRate)

        '''
        if we're about to encode past EOF, set chunkEnd to finish on the 
        last frame, and adjust 'frames' accordingly. else, continue

        if this next chunk is going to be the penultimate chunk, grow the
        job to subsume what would be the last truncated task. this task will
        be larger, but prevents any potential buggy behaviour with having a
        single frame end task. this calculation includes before/after buffer
        '''
        if (seek + (frameBufferSize * 2) + jobSize) > frameCountTotal:
            chunkEnd = frameCountTotal - seek
            frames = chunkEnd
            ## artifically decrement jobCount, since we're subsuming the last task
            jobCount -= 1
        else:
            chunkEnd = chunkStart + jobSize - 1
            frames = jobSize + (frameBufferSize * 2)

        counter += 1
        genFileString()

    logging.info("Encode Tasks: " + str(len(encodeTasks)))
    return encodeTasks, fileString


'''
calculate the position in the video (timewise) based on the frame we're
looking for and how many frames per second it runs at
'''
def fileSeek(pos, fps):
    newPos = round(pos / fps, 2)
    return newPos


'''
Queue up all tasks by calling 'encode.delay()', which is a Celery method for 
asynchronously queuing tasks in our message broker (RabbitMQ). 'encode' is 
referencing the custom function each Celery worker is carrying which excutes 
the ffmpeg task.

'encode.delay()' returns a handle to that task with methods for determing the 
state of the task. Handles are stored in 'statusHandles' list for later use.
'''
def populateQueue(q, encodeTasks):
    jobHandles = {}

    for task in encodeTasks:
        try:
            job = q.enqueue('tasks.encode', task, job_timeout=3600)
            logging.debug("Job ID: " + str(job.get_id()))
            jobHandles[job.get_id()] = job
        except:
            logging.info("populateQueue fail: " + str(task.exc_info))
 
    logging.info("Jobs queued: " + str(len(jobHandles)))
    
    return q, jobHandles


def testOnMessage(msg):
    if msg['status'] == "FINISHED":
        result = msg['result']
        logging.info(str(result['hostname']) + ': ' + str(result['fps']))


'''
wait for every task in 'resultSet' to return. on return, the task is directed 
to a callback function for handling the result, which in this case is x265's
average frames per second speed over the finished chunk of work.

if a task is 'revoked' (either by code or user via Flower GUI), handle that 
exception and assume the task was stuck and needs to be requeued. find the 
original task, resubmit it, add it to the resultSet object, and return to 
waiting for all tasks to finish
'''
def waitForTaskCompletion(q, jobDict):
    failRegistry = q.failed_job_registry
    completedJobs = []

    while True:
        for jobId, job in jobDict.items():
            if job.get_status() == 'finished':
                logging.info("ID: " + str(jobId))
                logging.info("FPS: " + str(job.result))
                completedJobs.append(jobId)
            if job.is_failed == True:
                failRegistry.requeue(job)
                logging.info("Requeued failed job " + jobId)


            sleep(0.2)
        sleep(3)

        ## remove any completed jobs from jobDict
        for item in completedJobs:
            del jobDict[item]

        ## clear completedJobs list
        del completedJobs[:]

        if len(jobDict) == 0:
            break


def rebuildVideo(target, fullOutboundPath):
    dirList = os.listdir(fullOutboundPath)
    dirFiles = [x for x in dirList if x[-4:] == '.265']
    dirFiles.sort()
    tempFileName = '_'.join(['noaudio', getFileName(target)])
    noAudioFilePath = '/'.join([fullOutboundPath, tempFileName])
    cmd = ['mkvmerge', '--output', noAudioFilePath]
    
    firstFlag = 0
    for chunk in dirFiles:
        path = '/'.join([fullOutboundPath, chunk])
        if firstFlag == 1:
            cmd.append('+')

        cmd.append(path)
        firstFlag = 1

    cmdString = ' '.join(cmd)
    logging.info("rebuildVideo() cmd: " + cmdString)
    os.system(cmdString)

    return noAudioFilePath


def mergeAudio(origFile, noAudioFile, fullOutboundPath):
    finalFileName = '/'.join([fullOutboundPath, getFileName(origFile)])
    cmd = ['mkvmerge', '--output', finalFileName, '-D', origFile, noAudioFile]

    cmdString = ' '.join(cmd)
    logging.info("mergeAudio() cmd: " + cmdString)
    os.system(cmdString)


def cleanOutFolder(fullOutboundPath):
    dirFiles = os.listdir(fullOutboundPath)
    for entry in dirFiles:
        if entry[-4:] == '.265':
            os.remove(''.join([fullOutboundPath, '/', entry]))
        if entry[0:7] == 'noaudio':
            os.remove(''.join([fullOutboundPath, '/', entry]))


def main():    
    redisLink = Redis('redis')
    q = Queue(connection=redisLink)

    completedFiles = []

    while True:
        checkFlag = 0
        files = findWork()

        ## check for files and clients
        if len(files) == 0:
            logging.debug("No files, sleeping")
            sleep(30)
            continue

        ## check if we've already worked these files
        for newFile in files:
            for oldFile in completedFiles:
                if newFile == oldFile:
                    checkFlag = 1
        if checkFlag == 1:
            logging.info("No new files, sleeping")
            sleep(30)
            continue

        ## are there workers available
        if getClientCount(redisLink) == 0:
            logging.debug("No workers, sleeping")
            sleep(30)
            continue

        ## work through files found
        for targetFile in files:
            ## check if client list has changed
            clientCount = getClientCount(redisLink)
            if clientCount == 0:
                logging.debug("No workers, sleeping")
                sleep(30)
                break

            frameCountTotal, frameRate, duration = getFrameCount(targetFile)
            encodeTasks, outboundFile = buildCmdString(targetFile, \
                    frameCountTotal, frameRate, duration, clientCount)

            ## determine full path of folder for video chunks
            tempPath = outboundFile.split('/')
            tempLen = len(tempPath)
            tempPath2 = '/'.join(tempPath[:tempLen-1])
            fullOutboundPath = '/'.join([outFolder, tempPath2])
            logging.info("Outbound path: " + str(fullOutboundPath))

            q, jobHandles = populateQueue(q, encodeTasks)

            logging.info("Waiting on jobs...")
            waitForTaskCompletion(q, jobHandles)

            logging.info("Building new Matroska file")
            noAudioFile = rebuildVideo(targetFile, fullOutboundPath)

            logging.info("Merging audio tracks into new MKV file")
            mergeAudio(targetFile, noAudioFile, fullOutboundPath)

            logging.info("Clearing out '.265' chunk files")
            cleanOutFolder(fullOutboundPath)

            logging.info("\nFinished " + targetFile)
            completedFiles.append(targetFile)
        


if __name__ == "__main__":
    main()


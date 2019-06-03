import re, os, math, sys
import shutil
import subprocess
import logging
from statistics import mode, StatisticsError
from pathlib import Path
from time import sleep
from redis import Redis
from rq import Queue, Worker


#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.basicConfig(level=logging.DEBUG)


class Task:
    def __init__(self, configDict):
        self.inbox = configDict['inbox']
        self.outbox = configDict['outbox']
        self.target = configDict['target']
        self.doneDir = configDict['doneDir']
        self.debug = configDict['debug']
        self.jobTimeout = configDict['jobTimeout']
        self.cropSampleCount = configDict['cropSampleCount']
        self.timeOffsetPercent = configDict['timeOffsetPercent']
        self.task_workers = configDict['workers']
        self.avgFps = {}

    '''
    return the base name of a filepath
    '''
    def _getFileName(self, name):
        return os.path.basename(name)


    '''
    find the number of frames in the video. this can be error prone, so multiple
    methods are attempted
    '''
    def getFrameCount(self):
        frameCount, frameRate, duration = None, None, None

        ## attempting mediainfo method
        mediaRet = subprocess.run(["mediainfo", "--fullscan", self.target], \
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
                self.target], stdout=subprocess.PIPE)

            frameCount = ffprobeRet.stdout.decode('utf-8')
        else:
            frameCount = mediaMatch.group(1)

        logging.info("Frame Count: " + str(frameCount))
        logging.info("Frame Rate: " + str(frameRate))
        logging.info("Duration: " + str(duration))
        logging.info("Calc FC: " + str(float(duration) * float(frameRate) / 1000))

        return int(frameCount), float(frameRate), int(duration)


    '''
    use ffmpeg to detect video cropping in target sample
    function attempts to do a sampling across the file by avoiding the beginning
    and end (intro/credits) and picks candidates distributed across the body
    where ffmpeg will have a better chance of detecting cropped frames, then
    looks for a majority consensus among the samples
    '''
    def _detectCropping(self, duration):
        offsets = []
        cropValues = []
        timeOffsetSeconds = duration * self.timeOffsetPercent
        sampleWindow = duration - (timeOffsetSeconds * 2)
        sampleSpread = sampleWindow / self.cropSampleCount
        
        ## calculate our time offsets for sampling cropping
        for seek in range(self.cropSampleCount):
            offsets.append(int((timeOffsetSeconds + (sampleSpread * seek)) / 1000))

        ## sampling loop
        for offset in offsets:
            cmdRet = subprocess.run(["ffmpeg", \
                            "-ss", str(offset), \
                            "-i", self.target, \
                            "-t", "10", \
                            "-vf", "cropdetect=24:16:0", \
                            "-preset", "ultrafast", \
                            "-f", "null", \
                            "-"], \
                        stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
            match = re.search(r'\s(crop=\d+\:\d+[^a-zA-Z]*?)\n', \
                cmdRet.stdout.decode('utf-8'))
        
            if match:
                cropValues.append(match.group(1))
                logging.debug("Found crop value: " + match.group(1))

        ## parse results
        if len(cropValues) == 0:
            logging.info("No cropping detected")
            return ''
        elif len(cropValues) < self.cropSampleCount:
            logging.info("Cropping failed to get full sampling!")
            logging.info("Proceeding with cropping disabled...")
            return ''
        else:
            try:
                result = mode(cropValues)
                logging.info("Cropping consensus: " + result)
                return result
            except StatisticsError:
                logging.info("No consensus found for cropping")
                logging.info("Try increasing sampling value")
                logging.info("Proceeding with cropping disabled...")
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
    def buildCmdString(self, frameCountTotal, frameRate, duration):
        encodeTasks = []
        fileString = ''
        frameBufferSize = 100
        jobSize = 300
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
        def _genFileString():
            nonlocal fileString
            namePart = ''
            fileName = self._getFileName(self.target)
            newFolder = fileName
            newPath = '/'.join([self.outbox, newFolder])
            os.makedirs(newPath, 0o777, exist_ok=True)
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
            #logging.debug("FileString: " + str(fileString))


        ## determine if cropping will be included
        crop = self._detectCropping(duration)
        if crop != '':
            tempCrop = crop
            crop = "-filter:v \"{}\"".format(tempCrop)

        ## initial values for first loop iteration
        seek, seconds, chunkStart = 0, 0, 0
        chunkEnd = jobSize - 1
        frames = jobSize + frameBufferSize
        _genFileString()

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
                    -i '{tr}' \
                    {cd} \
                    -strict \
                    -1 \
                    -f yuv4mpegpipe - | x265 - \
                    --log-level debug \
                    --no-open-gop \
                    --frames {fr} \
                    --chunk-start {cs} \
                    --chunk-end {ce} \
                    --colorprim bt709 \
                    --transfer bt709 \
                    --colormatrix bt709 \
                    --crf=19 \
                    --fps {frt} \
                    --min-keyint 24 \
                    --keyint 240 \
                    --sar 1:1 \
                    --preset slow \
                    --ctu 64 \
                    --y4m \
                    --pools \"+\" \
                    -o '{dst}/{fStr}'".format( \
                    tr = self.target, \
                    cd = crop, \
                    fr = frames, \
                    cs = chunkStart, \
                    ce = chunkEnd, \
                    ctr = counter, \
                    frt = frameRate, \
                    sec = seconds, \
                    dst = self.outbox, \
                    fStr = fileString)

            ## push built CLI command onto end of list
            encodeTasks.append(ffmpegStr)
            ## if debugging, cut out excess spaces from command string
            #logging.debug(' '.join(ffmpegStr.split()))

            chunkStart = frameBufferSize
            if counter == 0:
                seek = jobSize - chunkStart
                if seek < 0:
                    seek = 0
                seconds = self._fileSeek(seek, frameRate)
            else:
                seek = seek + jobSize
                seconds = self._fileSeek(seek, frameRate)

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
            _genFileString()

        logging.info("Encode Tasks: " + str(len(encodeTasks)))
        return encodeTasks, fileString


    '''
    calculate the position in the video (timewise) based on the frame we're
    looking for and how many frames per second it runs at
    '''
    def _fileSeek(self, pos, fps):
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
    def populateQueue(self, q, encodeTasks):
        jobHandles = []

        for task in encodeTasks:
            try:
                job = q.enqueue('tasks.encode', task, job_timeout=self.jobTimeout)
                #logging.debug("Job ID: " + str(job.get_id()))
                jobHandles.append((job.get_id(), job))
            except:
                logging.info("populateQueue fail: " + str(task.exc_info))
     
        logging.info("Jobs queued: " + str(len(jobHandles)))
        return q, jobHandles


    '''

    '''
    def calcAvgFps(self, fps, nodename):
        self.avgFps[nodename] = fps
        avg = 0

        ## wait for all workers to report before rolling calculations
        if len(self.avgFps) >= self.task_workers:
            avg = sum(float(val) for val in self.avgFps.values())
            #self.avgFps.clear()
            
        return avg


    '''
    tbd
    '''
    def waitForTaskCompletion(self, q, jobList):
        failRegistry = q.failed_job_registry
        deleteIndex = None
        pollSize = self.task_workers * 2 + 1
        jobNum = len(jobList)
        fpsAverage = 0
        counter = 1

        while True:
            newJob = None
            for i, (jobId, job) in enumerate(jobList):
                if job.get_status() == 'finished':
                    (fps, hostname, nodename) = job.result
                    ret = self.calcAvgFps(fps, nodename)
                    if ret > 0:
                        fpsAverage = ret

                    out = "{}/{} - FPS (Total) {} ({}) <{}> {}".format( \
                            str(counter), \
                            str(jobNum), \
                            str(fps), \
                            "{0:.2f}".format(fpsAverage), \
                            str(hostname), \
                            str(jobId))

                    logging.info(out)
                    deleteIndex = i
                    counter += 1
                    break
                ## re-queue a failed job by creating a new job, copying in the 
                ## original arguments, and mapping the original job ID onto it.
                ## then delete the old failed job, and requeue new job at front
                ## of queue
                elif job.is_failed == True:
                    argsParam = job.args
                    failRegistry.remove(job)    # is this still necessary?
                    q.remove(job)
                    job.delete()    # is this still necessary?
                    deleteIndex = i
                    logging.info("Requeing job {}".format(jobId))
                    newJob = q.enqueue_call('tasks.encode', args=argsParam, \
                            timeout=self.jobTimeout, job_id=jobId, at_front=True)
                    break
                elif job.get_status() != "queued" and job.get_status() != "started":
                    logging.debug("Undocumneted Job Status: " + str(job.get_status()))

                if i > pollSize:
                    break

                sleep(0.5)


    #        logging.debug("jobList Size: " + str(len(jobList)))
            if len(jobList) == 0:
                break
            elif deleteIndex != None:
                ## remove completed job from jobList
    #            (k, v) = jobList[deleteIndex]
    #            logging.debug("Deleting " + str(k))
                del jobList[deleteIndex]
                deleteIndex = None

            ## place requeued (reconstituted) job at beginning of our job list
            ## must be in front, else polling may miss the status change
            if not newJob == None:
                jobList.insert(0, (newJob.id, newJob))

            sleep(1)



    '''
    use mkvmerge tool to piece together the completed video from chunk files.
    function builds the command string, which ends up in the format:
        mkvmerge --output <outpath> chunk_1 + chunk_2 + chunk_N

    output file is named "noaudio_<video file name>"
    '''
    def rebuildVideo(self, fullOutboundPath):
        quiet = None
        dirList = os.listdir(fullOutboundPath)
        dirFiles = [x for x in dirList if x[-4:] == '.265']
        dirFiles.sort()
        tempFileName = '_'.join(['noaudio', self._getFileName(self.target)])
        noAudioFilePath = '/'.join([fullOutboundPath, tempFileName])
        if self.debug:
            quiet = ''
        else:
            quiet = '--quiet'

        cmd = ['mkvmerge', quiet, '--output', r'"{}"'.format(noAudioFilePath)]

        firstFlag = 0
        for chunk in dirFiles:
            path = '/'.join([fullOutboundPath, chunk])
            if firstFlag == 1:
                cmd.append('+')

            cmd.append(r'"{}"'.format(path))
            firstFlag = 1

        cmdString = ' '.join(cmd)
        #logging.debug("rebuildVideo() cmd: " + cmdString)
        os.system(cmdString)

        return noAudioFilePath


    '''
    tbd
    '''
    def mergeAudio(self, noAudioFile, fullOutboundPath):
        quiet = None
        finalFileName = '/'.join([fullOutboundPath, self._getFileName(self.target)])
        if self.debug:
            quiet = ''
        else:
            quiet = '--quiet'

        cmd = ['mkvmerge', quiet, '--output', \
                r'"{}"'.format(finalFileName), \
                '-D', \
                r'"{}"'.format(self.target), \
                r'"{}"'.format(noAudioFile)]

        cmdString = ' '.join(cmd)
        #logging.debug("mergeAudio() cmd: " + cmdString)
        os.system(cmdString)

        return finalFileName


    '''
    Verify that a new video file is in the destination folder, and if so
    delete all video chunks and surplus data
    '''
    def cleanOutFolder(self, fullOutboundPath, finalFileName):
        video = Path(finalFileName)
        if not video.is_file():
            logging.error("Cannot find completed video file. Delaying cleaning")
        else:
            dirFiles = os.listdir(fullOutboundPath)
            for entry in dirFiles:
                if entry[-4:] == '.265':
                    os.remove(''.join([fullOutboundPath, '/', entry]))
                if entry[0:7] == 'noaudio':
                    os.remove(''.join([fullOutboundPath, '/', entry]))


    '''
    creates a "done" directory and moves the completed video file into the folder.
    '''
    def indicateCompleted(self):
        ## check if completed folder is present
        doneFolder = '/'.join([self.inbox, self.doneDir])
        os.makedirs(doneFolder, 0o777, exist_ok=True)

        ## move source file to 'done' folder in inbox
        moveTargetPath = '/'.join([doneFolder, self._getFileName(self.target)])
        logging.debug("file move: " + moveTargetPath)
        shutil.move(self.target, moveTargetPath)



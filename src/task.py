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
#logging.basicConfig(level=logging.DEBUG)


class Task:
    def __init__(self, configDict):
        self.threadId = configDict['threadId']
        self.inbox = configDict['inbox']
        self.outbox = configDict['outbox']
        self.target = configDict['target']
        self.doneDir = configDict['doneDir']
        self.logLevel = configDict['logLevel']
        self.jobTimeout = configDict['jobTimeout']
        self.cropSampleCount = configDict['cropSampleCount']
        self.timeOffsetPercent = configDict['timeOffsetPercent']
        self.task_workers = configDict['workers']
        self.jobSize = configDict['jobSize']
        self.frameBufferSize = configDict['frameBufferSize']

        self.avgFps = {}
        self.outboundFolderPath = self.MakeOutboundFolderPath()
        self.taskLogger = logging.getLogger("ute.task-" + str(self.threadId))


    '''
    return the base name of a filepath
    '''
    def _getFileName(self, name):
        return os.path.basename(name)


    '''
    return the name of this file's outbox folder, and make it if it doesn't
    yet exist in outbox
    '''
    def MakeOutboundFolderPath(self):
        newFolder = self._getFileName(self.target)
        newPath = '/'.join([self.outbox, newFolder])
        ## make it if it doesn't exist
        try:
            os.makedirs(newPath, 0o777, exist_ok=True)
        except(FileNotFoundError) as error:
            self.taskLogger.info("Unable to create 'outbox' folder or subfolder")
            self.taskLogger.error("Error: {}".format(str(error)))
            return False

        return newPath


    '''
    find the number of frames in the video. this can be error prone, so multiple
    methods are attempted
    '''
    def getFrameCount(self, method='default'):
        frameCount, frameRate, duration = None, None, None
        mediaRet, mediaMatch, mediaFrameRate, mediaDuration = None, None, None, None

        if method == 'default' or method == 'mediainfo':
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
        if mediaMatch == None or method == 'ffprobe':
            self.taskLogger.info("Using ffprobe")
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

        self.taskLogger.info("Frame Count: {}".format(str(frameCount)))
        self.taskLogger.info("Frame Rate: {}".format(str(frameRate)))
        self.taskLogger.info("Duration: {}".format(str(duration)))
        self.taskLogger.info("Calc FC: {}".format(str(float(duration) * float(frameRate) / 1000)))

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
                self.taskLogger.debug("Found crop value: {}".format(match.group(1)))

        ## parse results
        if len(cropValues) == 0:
            self.taskLogger.info("No cropping detected")
            return ''
        elif len(cropValues) < self.cropSampleCount:
            self.taskLogger.info("Cropping failed to get full sampling!")
            self.taskLogger.info("Proceeding with cropping disabled")
            return ''
        else:
            try:
                result = mode(cropValues)
                self.taskLogger.info("Cropping consensus: {}".format(str(result)))
                return result
            except StatisticsError:
                self.taskLogger.info("No consensus found for cropping")
                self.taskLogger.info("Try increasing sampling value")
                self.taskLogger.info("Proceeding with cropping disabled")
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
        frameBufferSize = self.frameBufferSize
        jobSize = self.jobSize
        jobCount = int(math.ceil(frameCountTotal / jobSize))
        chunkPaths = [] ## store paths of each encoded chunk for checking later
        counter = 0

        ## handle two special cases that mess up encoding. Exit if either is true
        if jobSize < frameBufferSize:
            self.taskLogger.error("Error: jobSize must be at least as large as frameBufferSize")
            sys.exit()
        if jobCount < 3:
            self.taskLogger.error("Error: jobCount must be higher. Please decrease jobSize")
            sys.exit()

        ## build the output string for each chunk
        def _genFileString():
            nonlocal fileString
            namePart = ''
            fileName = self._getFileName(self.target)
            newFolder = fileName

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
            #self.taskLogger.debug("FileString: {}".format(str(fileString)))


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

        self.taskLogger.debug("jobCount / jobSize / frameCountTotal: {}/{}/{}".format( \
                str(jobCount), \
                str(jobSize), \
                str(frameCountTotal)))

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
                    --log-level info \
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
            encodeTasks.append(' '.join(ffmpegStr.split()))
            ## if debugging, cut out excess spaces from command string
            #self.taskLogger.debug(' '.join(ffmpegStr.split()))

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

            ## create a seperate array of chunk file paths for checking our work later
            chunkPaths.append((self.outbox, fileString))

            counter += 1
            _genFileString()

        self.taskLogger.info("Encode Tasks: {}".format(str(len(encodeTasks))))
        return encodeTasks, fileString, chunkPaths


    '''
    calculate the position in the video (timewise) based on the frame we're
    looking for and how many frames per second it runs at
    '''
    def _fileSeek(self, pos, fps):
        newPos = round(pos / fps, 2)
        return newPos


    '''
    look for existing files in the target's outbound folder. if present, count the number
    of jobs done, then attempt to estimate where to pick up by finding the number of 
    workers present, and subtract that from the jobs found. pass the encodeTasks
    list back with a starting index reflecting a subtraction of jobs found.
    '''
    def CheckForPriorWork(self, encodeTasks, chunkPaths, numOfWorkers):
        ## var for determining our starting point in encodeTasks
        startIndex = 0

        for folder, chunk in chunkPaths:
            if os.path.isfile('/'.join([folder, chunk])):
                startIndex += 1

        if startIndex > 0:
            '''
            since we've found existing progress, we're not sure which was the last fully
            completed chunk. So we're going to assume that if there are 'n' number of 
            workers available, they're probably the same number of workers from last 
            time. Tasks done minus 'n'. But, just in case one worker is slower than the 
            others, or we have a very small number or workers, lets just multiply them 
            by three, and repeat that work.

            If very little work was done, then forget it and just restart at 0.
            '''
            if startIndex > numOfWorkers * 3:
                startIndex -= numOfWorkers * 1
            else:
                startIndex = 0

            self.taskLogger.info("Found existing work. Picking up at {}".format(str(startIndex)))

        return encodeTasks, encodeTasks[startIndex:]


    '''
    Queue up all tasks by calling 'encode.delay()', which is a Celery method for 
    asynchronously queuing tasks in our message broker (RabbitMQ). 'encode' is 
    referencing the custom function each Celery worker is carrying which excutes 
    the ffmpeg task.

    'encode.delay()' returns a handle to that task with methods for determing the 
    state of the task. Handles are stored in 'statusHandles' list for later use.
    '''
    def PopulateQueue(self, q, encodeTasks):
        jobHandles = []

        for task in encodeTasks:
            try:
                job = q.enqueue('worker.encode', task, job_timeout=self.jobTimeout)
                #self.taskLogger.debug("Job ID: {}".format(str(job.get_id())))
                jobHandles.append((job.get_id(), job))
            except:
                self.taskLogger.info("PopulateQueue fail: {}".format(str(task.exc_info)))
     
        self.taskLogger.info("Jobs queued: {}".format(str(len(jobHandles))))
        return q, jobHandles


    '''
    Make an attempt to determine how many frames per second are being computed on the 
    stack. We don't have instantaneous insight into every node's x265 encoder output, 
    but we get each node's average FPS upon completion of a job.

    AvgFps calculation begins as soon as we've seen FPS numbers from every node in
    the cluster, and starts a rolling average with each new figure.
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
    poll tasks for their status, and requeue them if there's a failure
    '''
    def WaitForTaskCompletion(self, q, jobList):
        failRegistry = q.failed_job_registry
        deleteIndex = None
        pollSize = self.task_workers * 2 + 1
        jobNum = len(jobList)
        fpsAverage = 0
        counter = 1

        while True:
            newJob = None
            '''
            continuoulsy iterate over every job object in 'jobList', polling
            its status to see if it has finished, failed, or otherwise. When
            we get a status, decide how to proceed.
            '''
            for i, (jobId, job) in enumerate(jobList):
                '''
                if job was sucessful, get returned worker data, print a
                status update, and add the job to the deleted items list so 
                we won't have to poll its status anymore. then break out
                of loop.
                '''
                if job.get_status() == 'finished':
                    ## get return values back from worker
                    (fps, hostname, nodename) = job.result

                    ## get FPS average data
                    ret = self.calcAvgFps(fps, nodename)
                    if ret > 0:
                        fpsAverage = ret

                    ## build status progress string
                    out = "{}/{} - FPS/Total: {} / {} \t<{}> || {}".format( \
                            str(counter), \
                            str(jobNum), \
                            str(fps), \
                            "{0:.2f}".format(fpsAverage), \
                            str(hostname), \
                            str(jobId))

                    self.taskLogger.info(out)

                    ## store index of completed job for removal
                    deleteIndex = i
                    counter += 1
                    break
                elif job.is_failed == True:
                    '''
                    re-queue a failed job by creating a new job, copying in the 
                    original arguments, and mapping the original job ID onto it.
                    then delete the old failed job, and requeue new job at front
                    of queue
                    '''
                    ## get job info before we delete it
                    argsParam = job.args
                    failRegistry.remove(job)
                    q.remove(job)

                    ## cancels the job and deletes job hash from Redis
                    job.delete()

                    ## build and requeue a new instance of that job
                    newJob = self.RequeueJob(argsParam, q, jobId)
                    deleteIndex = i
                    break
                elif job.get_status() != "queued" and job.get_status() != "started":
                    '''
                    catch cases where the job status is not 'finished' or 'failed', and 
                    log info about this job and context. then attempt to pass the job off
                    as complete and let the error checking figure it out later.
                    '''
                    self.taskLogger.debug("Unknown Job Status: {} - jobId: {}".format( \
                          str(job.get_status()),
                          str(jobId)))

                    ## store index of completed job for removal
                    deleteIndex = i
                    counter += 1


                ## if index is larger than the size of our (shrinking) list, break
                if i > pollSize:
                    break

                ## pause before polling RQ again
                sleep(0.1)


            #self.taskLogger.debug("jobList Size: {}".format(str(len(jobList))))
            if len(jobList) == 0:
                break
            elif deleteIndex != None:
                del jobList[deleteIndex]
                deleteIndex = None

            '''
            place requeued (reconstituted) job at beginning of our job list
            must be in front, else polling may miss the status change
            '''
            if not newJob == None:
                jobList.insert(0, (newJob.id, newJob))

            sleep(1)


    '''
    Requeue a job on Redis/RQ, and ensure it is positioned for immediate processing
    '''
    def RequeueJob(self, argsParam, q, jobId):
        '''
        wait a second for the worker that failed to pick a different job off 
        the queue, just in case there's an issue with it.
        '''
        import pprint

        self.taskLogger.info("Requeing job {}".format(str(jobId)))
        pp = pprint.PrettyPrinter()
        ppArgsParam = pp.pformat(argsParam)
        self.taskLogger.info("args: {}".format(str(ppArgsParam)))

        newJob = q.enqueue_call('worker.encode', args=argsParam, \
                timeout=self.jobTimeout, job_id=jobId, at_front=True)

        return newJob


    '''
    Determine if any of the completed file chunks failed to properly encode. Issues
    normally manifest themselves in the form of very small or zero byte files.

    Chunks found to be malformed are requeued for immediate processing.

    CheckWork() is given a reference to "returnArray" and an offset for writing to that array.
    This is important since several threaded instances of CheckWork will be running
    concurrently, and need to dump their results into one location without stomping on any
    other instance. So in this case, CheckWork() does not explicitly return anything.

    @Return:
        N/A
    '''
    def CheckWork(self, chunkPaths, jobHandles, returnArray, returnIndex):
        redoJobs = []

        '''
        Look at file sizes, and anything below 10 KB in size, consider a failed chunk.
        Theoretically, the last chunk should never be smaller than the designated frame
        buffer size set in configuration, and won't be under 10 KB.

        'chunkPaths is comprised of 'folder' which is the path to the outbox, and 'chunk',
        which is the subpath of "title folder/chunk name"
        '''
        for folder, chunk in chunkPaths:
            missing = False
            frameCountExpected = 1
            frameCountFound = 0
            fSize = 0
            path = '/'.join([folder, chunk])

            try:
                ## if only file header found, consider missing
                fSize = os.path.getsize('/'.join([folder, chunk]))
                if fSize < 4 * 1024:
                    missing = True
                #elif fSize < 500 * 1024:
                else:
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
                        path], stdout=subprocess.PIPE)

                    frameCountFound = ffprobeRet.stdout.decode('utf-8')
                    frameCountFound = int(frameCountFound) - 1  ## off by one
                    data = subprocess.run(["mediainfo", "--fullscan", path], stdout=subprocess.PIPE)
                    chunkStart = re.search("chunk-start\=(\d+)", data.stdout.decode('utf-8'))
                    chunkEnd = re.search("chunk-end\=(\d+)", data.stdout.decode('utf-8'))
                    totalFrames = re.search("total-frames\=(\d+)", data.stdout.decode('utf-8'))

                    '''
                    on first chunk, chunkStart isn't specififed since it starts from frame 0. So 
                    we'll just use chunkEnd as the expected frame count.
                    '''
                    if chunkStart == None: 
                        try:
                            frameCountExpected = int(chunkEnd.group(1)) - 1  ## off by one
                        except AttributeError as error:
                            missing = True
                            self.taskLogger.warning("chunkStart, chunkEnd missing! :: {}".format(
                                str(error)))
                            self.taskLogger.warning("chunk: {}".format(str(path)))
                            self.taskLogger.warning("flagging chunk for requeue")
                            self.taskLogger.debug("data : " + str(data.stdout.decode('utf-8')))
                    elif chunkEnd == None:
                        try:
                            frameCountExpected = int(totalFrames) - int(chunkStart.group(1))
                            self.taskLogger.debug("total-frames: " + str(totalFrames))
                            self.taskLogger.debug("chunk: " + str(path))
                        except AttributeError as error:
                            missing = True
                            self.taskLogger.error("chunkStart, chunkEnd missing! Error: {}".format(
                                str(error)))
                            self.taskLogger.error("chunk: " + str(path))
                    else:
                        #self.taskLogger.debug("chunkStart: " + chunkStart.group(1))
                        #self.taskLogger.debug("chunkEnd: " + chunkEnd.group(1))
                        frameCountExpected = int(chunkEnd.group(1)) - int(chunkStart.group(1))

                    self.taskLogger.debug("Frames Found/Expected: {}/{} :: {}".format(
                        str(frameCountFound), str(frameCountExpected), str(path)))
            except FileNotFoundError:
                ## exception handling in desperate need of reworking
                self.taskLogger.info("Missing chunk {}".format(str(chunk)))
                missing = True


            '''
            grab the file's number from its name, and use it to locate the
            correct index of the tuple (job_id, job) in jobHandles. push
            the tuple to redoJobs for reprocessing
            '''
            #if  missing or fSize < 10 * 1024:
            if missing or (int(frameCountExpected) != int(frameCountFound)):
                self.taskLogger.info("Found failed job {}".format(str(chunk)))
                chunkName = chunk.split('/')[1]
                chunkNumber = re.match('^(\d{3,7})_.*\.265', chunkName)
                offender = jobHandles[int(chunkNumber.group(1))]
                redoJobs.append(offender)


        returnArray[returnIndex] = redoJobs
        #return redoJobs


    '''
    use mkvmerge tool to piece together the completed video from chunk files.
    function builds the command string, which ends up in the format:
        mkvmerge --output <outpath> chunk_1 + chunk_2 + chunk_N

    output file is named "noaudio_<video file name>"
    '''
    def RebuildVideo(self, fullOutboundPath):
        quiet = None
        dirList = os.listdir(fullOutboundPath)
        dirFiles = [x for x in dirList if x[-4:] == '.265']
        dirFiles.sort()
        tempFileName = '_'.join(['noaudio', self._getFileName(self.target)])
        noAudioFilePath = '/'.join([fullOutboundPath, tempFileName])
        if self.logLevel == logging.DEBUG:
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
        self.taskLogger.debug("rebuildVideo() cmd: {}".format(str(cmdString)))
        os.system(cmdString)

        return noAudioFilePath


    '''
    use mkvmerge to recombine the newly merged video track with the original audio track
    from the source.
    '''
    def MergeAudio(self, noAudioFile, fullOutboundPath):
        quiet = None
        finalFileName = '/'.join([fullOutboundPath, self._getFileName(self.target)])
        ## append a status indicator so users know the file is still being built
        tempFileName = finalFileName + '.processing'
        if self.logLevel == logging.DEBUG:
            quiet = ''
        else:
            quiet = '--quiet'

        cmd = ['mkvmerge', quiet, '--output', \
                r'"{}"'.format(tempFileName), \
                '-D', \
                r'"{}"'.format(self.target), \
                r'"{}"'.format(noAudioFile)]

        cmdString = ' '.join(cmd)
        self.taskLogger.debug("mergeAudio() cmd: {}".format(str(cmdString)))
        os.system(cmdString)

        ## remove status indicator from filename
        os.rename(tempFileName, finalFileName)

        return finalFileName


    '''
    Verify that a new video file is in the destination folder, and if so
    delete all video chunks and surplus data
    '''
    def CleanOutFolder(self, fullOutboundPath, finalFileName):
        video = Path(finalFileName)
        if not video.is_file():
            self.taskLogger.error("Cannot find completed video file. Delaying cleaning")
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
    def IndicateCompleted(self):
        ## check if completed folder is present
        doneFolder = '/'.join([self.inbox, self.doneDir])
        os.makedirs(doneFolder, 0o777, exist_ok=True)

        ## move source file to 'done' folder in inbox
        moveTargetPath = '/'.join([doneFolder, self._getFileName(self.target)])
        self.taskLogger.debug("file move: {}".format(str(moveTargetPath)))
        shutil.move(self.target, moveTargetPath)



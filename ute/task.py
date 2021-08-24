import re, os, math, sys
import shutil
import subprocess
from logging import getLogger, DEBUG
from typing import List, Dict, Tuple, Any
from statistics import mode, StatisticsError
from pathlib import Path
from time import sleep
from redis import Redis
from rq import Queue, Worker, Job

import ute.files


class Task:
    def __init__(self, configDict: Dict[str, Any]):
        self.thread_id = configDict['threadId']
        self.inbox = configDict['inbox']
        self.outbox = configDict['outbox']
        self.vsScripts_path = configDict['vsScripts_path']
       # self.vsScriptName = configDict['vsScriptName']
        self.target = configDict['target']
        self.done_dir = configDict['doneDir']
        self.log_level = configDict['logLevel']
       # self.jobTimeout = configDict['jobTimeout']
       # self.cropSampleCount = configDict['cropSampleCount']
       # self.timeOffsetPercent = configDict['timeOffsetPercent']
        self.task_workers = configDict['worker_count']
       # self.jobSize = configDict['jobSize']
       # self.frameBufferSize = configDict['frameBufferSize']
       # self.vapoursynth = configDict['vapoursynthEnabled']

        self.average_fps = {}
        self.outbound_folder_path = self.MakeOutboundFolderPath()
        self.logger = getLogger("ute.task-" + str(self.thread_id))



    def buildCommandString(self, 
                        frame_count_total: int, 
                        frame_rate: float, 
                        duration: int) -> Tuple[List[str], str, List[Tuple[str, str]]]:
        '''
        Build the ffmpeg/x265 command line parameters, filling in relevant variables
        for user-defined encode settings, file location, output naming scheme, etc. 
        Each completed string is pushed onto an array for later delivery to RabbitMQ 
        as a task for Celery workers.

        << ffmpeg / x265 argument variables >>

        file_string -- the name of the file chunk generated: <num>_chunk_<filename>.265
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
        encode_tasks = []
        file_string, vsScriptPath, vsPipeStr = '', '', ''
        frame_buffer_size = self.frameBufferSize
        ffmpeg_target = "-i \'" + self.target + '\''
        job_size = self.jobSize
        job_count = int(math.ceil(frame_count_total / job_size))
        chunk_paths = [] ## store paths of each encoded chunk for checking later
        counter = 0

        ## handle two special cases that mess up encoding. Exit if either is true
        if job_size < frame_buffer_size:
            self.logger.error("Error: jobSize must be at least as large as frameBufferSize")
            sys.exit()
        if job_count < 3:
            self.logger.error("Error: jobCount must be higher. Please decrease jobSize")
            sys.exit()

        ## build the output string for each chunk
        def _genFileString():
            nonlocal file_string
            namePart = ''
            fileName = self._getFileName(self.target)
            new_folder = fileName

            ## use part (or all) of the filename to help name chunks
            if len(fileName) > 10:
              namePart = fileName[0:9]
            else:
              namePart = fileName

            numLen = len(str(job_count))

            ## prepend each name with a zero-padded number for ordering. pad
            ## the number with the amount of digit locations we need
            file_string = new_folder + '/'
            if numLen <= 3:
                file_string += ''.join("{:03d}".format(counter))
            elif numLen == 4:
                file_string += ''.join("{:04d}".format(counter))
            elif numLen == 5:
                file_string += ''.join("{:05d}".format(counter))
            elif numLen == 6:
                file_string += ''.join("{:06d}".format(counter))
            else:
                file_string += ''.join("{:07d}".format(counter))

            file_string += "_" + namePart + ".265"
            #self.logger.debug("FileString: {}".format(str(fileString)))


        ## determine if cropping will be included
        crop = self._detectCropping(duration)
        if crop != '':
            tempCrop = crop
            crop = "-filter:v \"{}\"".format(tempCrop)

        ## initial values for first loop iteration
        seek, seconds, chunk_start, vsFrameStart = 0, 0, 0, 0
        chunk_end = job_size - 1
        vsFrameEnd = job_size + frame_buffer_size
        frames = job_size + frame_buffer_size
        _genFileString()

        if self.vapoursynth:
            vsScriptPath = '/'.join([self.vsScripts, self.vsScriptName])
            ffmpeg_target = '-i -'
            vsTarget = "UTE_TARGET=" + '\'' + self.target + '\''

        self.logger.debug("jobCount / jobSize / frameCountTotal: {}/{}/{}".format( 
                str(job_count), 
                str(job_size), 
                str(frame_count_total)))

        ## ffmpeg and x265 CLI args, with placeholder variables defined in the 
        ## .format() method below
        while counter <= job_count:
            if self.vapoursynth:
                try:
                    seconds = 0   ## set ffmpeg index to zero each time
                    if vsFrameEnd == -1:
                        vsEndArg = ''
                    else:
                        vsEndArg = '--end ' + str(vsFrameEnd)

                    vsPipeStr = "vspipe --start {fs} {fe} --arg {tar} {sp} - --y4m | ".format(  
                                fs = vsFrameStart,
                                fe = vsEndArg,
                                #fe = vsFrameEnd,
                                tar = vsTarget,
                                sp = vsScriptPath)
                except Exception as e:
                  self.logger.error("Vapoursynth string build failed: {}".format(str(e)))

            try:
                ffmpegStr = "{vs} ffmpeg \
                        -hide_banner \
                        -loglevel fatal \
                        -ss {sec} \
                        {tr} \
                        {cd} \
                        -strict \
                        -1 \
                        -f yuv4mpegpipe - | x265 \
                        --log-level info \
                        --no-open-gop \
                        --frames {fr} \
                        --chunk-start {cs} \
                        --chunk-end {ce} \
                        --colorprim bt709 \
                        --transfer bt709 \
                        --colormatrix bt709 \
                        --crf=22 \
                        --fps {frt} \
                        --min-keyint 24 \
                        --keyint 240 \
                        --sar 1:1 \
                        --preset slow \
                        --ctu 64 \
                        --y4m \
                        --pools \"+\" \
                        -o '{dst}/{fStr}' - ".format(
                        vs = vsPipeStr,
                        tr = ffmpeg_target, 
                        cd = crop, 
                        fr = frames, 
                        cs = chunk_start, 
                        ce = chunk_end, 
                        ctr = counter, 
                        frt = frame_rate, 
                        sec = seconds, 
                        dst = self.outbox, 
                        fStr = file_string)
            except Exception as e:
              self.logger.error("FFmpeg/x265 string build failed: {}".format(str(e)))

            ## push built CLI command onto end of list
            encode_tasks.append(' '.join(ffmpegStr.split()))
            ## if debugging, cut out excess spaces from command string
            self.logger.debug(' '.join(ffmpegStr.split()))

            chunk_start = frame_buffer_size
            if counter == 0:
                seek = job_size - chunk_start
                vsFrameStart = seek
                if seek < 0:
                    seek = 0
                    vsFrameStart = 0
                seconds = self.GetPosition(seek, frame_rate)
            else:
                seek = seek + job_size
                vsFrameStart = seek
                seconds = self.GetPosition(seek, frame_rate)

            '''
            if we're about to encode past EOF, set chunkEnd to finish on the 
            last frame, and adjust 'frames' accordingly. else, continue

            if this next chunk is going to be the penultimate chunk, grow the
            job to subsume what would be the last truncated task. this task will
            be larger, but prevents any potential buggy behaviour with having a
            single frame end task. this calculation includes before/after buffer
            '''
            if (seek + (frame_buffer_size * 2) + job_size) > frame_count_total:
                chunk_end = frame_count_total - seek -  1
                frames = chunk_end
                #vsFrameEnd = frameCountTotal - 1
                ## signal to routine above that we're at the end, so it will remove
                ## this argument from vspipe. vspipe doesn't seem to like explicitly
                ## ending on the last frame
                vsFrameEnd = -1
                ## artifically decrement jobCount, since we're subsuming the last task
                job_count -= 1
                self.logger.debug("chunkEnd {}, frames {}, vsFrameEnd {}".format(
                    str(chunk_end), str(frames), str(vsFrameEnd)))
            else:
                chunk_end = chunk_start + job_size - 1
                frames = job_size + (frame_buffer_size * 2)
                vsFrameEnd = seek + job_size + (frame_buffer_size * 2)

            ## create a seperate array of chunk file paths for checking our work later
            chunk_paths.append((self.outbox, file_string))

            counter += 1
            _genFileString()

        self.logger.info("Encode Tasks: {}".format(str(len(encode_tasks))))
        return encode_tasks, file_string, chunk_paths



    def checkForPriorWork(self, chunk_paths: List[str], number_of_workers: int) -> int:
        '''
        look for existing files in the target's outbound folder. if present, count the number
        of jobs done, then attempt to estimate where to pick up by finding the number of 
        workers present, and subtract that from the jobs found. pass the encodeTasks
        list back with a starting index reflecting a subtraction of jobs found.
        '''
        start_index = 0

        for folder, chunk in chunk_paths:
            if os.path.isfile('/'.join([folder, chunk])):
                start_index += 1

        if start_index > 0:
            '''
            since we've found existing progress, we're not sure which was the last fully
            completed chunk. So we're going to assume that if there are 'n' number of 
            workers available, they're probably the same number of workers from last 
            time. Tasks done minus 'n'. If we miss a half-done job, it ought to be
            caught by CheckWork()

            If very little work was done, then forget it and just restart at 0.
            '''
            if start_index > number_of_workers:
                start_index -= number_of_workers
            else:
                start_index = 0

            self.logger.info("Found existing work. Picking up at {}".format(str(start_index)))

        return start_index


    def populateQueue(self, queue: Queue, encode_tasks: List[str]) -> List[Tuple[str, Job]]:
        '''
        Queue up all tasks by calling 'encode.delay()', which is a Celery method for 
        asynchronously queuing tasks in our message broker (RabbitMQ). 'encode' is 
        referencing the custom function each Celery worker is carrying which excutes 
        the ffmpeg task.

        'encode.delay()' returns a handle to that task with methods for determing the 
        state of the task. Handles are stored in 'statusHandles' list for later use.
        '''
        job_handles = []

        for task in encode_tasks:
            try:
                job = queue.enqueue('worker.encode', task, job_timeout=self.jobTimeout)
                job_handles.append((job.get_id(), job))
            except:
                self.logger.info("PopulateQueue fail: {}".format(str(task.exc_info)))
     
        self.logger.info("Jobs queued: {}".format(str(len(job_handles))))
        #return queue, jobHandles
        return job_handles



    def waitForTaskCompletion(self, queue: Queue, job_list: List[Tuple[str, Job]]) -> None:
        '''
        poll tasks for their status, and requeue them if there's a failure
        '''

        fail_registry = q.failed_job_registry
        delete_index = None
        poll_size = self.task_workers * 2 + 1
        jobNum = len(job_list)
        fpsAverage = 0
        counter = 1

        while True:
            new_job = None
            '''
            continuoulsy iterate over every job object in 'jobList', polling
            its status to see if it has finished, failed, or otherwise. When
            we get a status, decide how to proceed.
            '''
            for i, (jobId, job) in enumerate(job_list):
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
                    ret = self.calculateAverageFps(fps, nodename)
                    if ret > 0:
                        fpsAverage = ret

                    ## build status progress string
                    out = "{}/{} - FPS/Total: {}/{}\t<{}> | {}".format( 
                            str(counter), 
                            str(jobNum), 
                            str(fps), 
                            "{0:.2f}".format(fpsAverage), 
                            str(hostname), 
                            str(jobId))

                    self.logger.info(out)

                    ## store index of completed job for removal
                    delete_index = i
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
                    fail_registry.remove(job)
                    q.remove(job)

                    ## cancels the job and deletes job hash from Redis
                    job.delete()

                    ## build and requeue a new instance of that job
                    new_job = self.requeueJob(argsParam, q, jobId)
                    delete_index = i
                    break
                elif job.get_status() != "queued" and job.get_status() != "started":
                    '''
                    catch cases where the job status is not 'finished' or 'failed', and 
                    log info about this job and context. then attempt to pass the job off
                    as complete and let the error checking figure it out later.
                    '''
                    self.logger.debug("Unknown Job Status: {} - jobId: {}".format( 
                          str(job.get_status()),
                          str(jobId)))

                    ## store index of completed job for removal
                    delete_index = i
                    counter += 1


                ## if index is larger than the size of our (shrinking) list, break
                if i > poll_size:
                    break

                ## pause before polling RQ again
                sleep(0.1)


            #self.logger.debug("jobList Size: {}".format(str(len(jobList))))
            if len(job_list) == 0:
                break
            elif delete_index != None:
                del job_list[delete_index]
                delete_index = None

            '''
            place requeued (reconstituted) job at beginning of our job list
            must be in front, else polling may miss the status change
            '''
            if not new_job == None:
                job_list.insert(0, (new_job.id, new_job))

            sleep(1)


    def calculateAverageFps(self, fps: float, nodename: str) -> float:
        '''
        Make an attempt to determine how many frames per second are being computed on the 
        stack. We don't have instantaneous insight into every node's x265 encoder output, 
        but we get each node's average FPS upon completion of a job.

        AvgFps calculation begins as soon as we've seen FPS numbers from every node in
        the cluster, and starts a rolling average with each new figure.
        '''
        self.average_fps[nodename] = fps
        avg = 0.0

        ## wait for all workers to report before rolling calculations
        if len(self.average_fps) >= self.task_workers:
            avg = sum(float(val) for val in self.average_fps.values())
            #self.avgFps.clear()
            
        return avg


    def requeueJob(self, argsParam, queue: Queue, job_id: str) -> Job:
        '''
        Requeue a job on Redis/RQ, and ensure it is positioned for immediate processing

        Wait a second for the worker that failed to pick a different job off 
        the queue, just in case there's an issue with it.
        '''
        import pprint

        self.logger.info("Requeing job {}".format(str(job_id)))
        pp = pprint.PrettyPrinter()
        ppArgsParam = pp.pformat(argsParam)
        self.logger.info("args: {}".format(str(ppArgsParam)))

        new_job = queue.enqueue_call('worker.encode', args=argsParam, 
                timeout=self.jobTimeout, job_id=job_id, at_front=True)

        return new_job




    def checkWork(self, 
                chunk_paths: List[Tuple[str, str]], 
                job_handles: List[Tuple[str, Job]], 
                return_array: List[list], 
                return_index: int) -> None:
        '''
        Determine if any of the completed file chunks failed to properly encode. Issues
        normally manifest themselves in the form of very small or zero byte files.

        Chunks found to be malformed are requeued for immediate processing.

        CheckWork() is given a reference to "returnArray" and an offset for writing to that array.
        This is important since several threaded instances of CheckWork will be running
        concurrently, and need to dump their results into one location without stomping on any
        other instance. So in this case, CheckWork() does not explicitly return anything.
        '''
        redo_jobs = []

        '''
        Look at file sizes, and anything below 10 KB in size, consider a failed chunk.
        Theoretically, the last chunk should never be smaller than the designated frame
        buffer size set in configuration, and won't be under 10 KB.

        'chunkPaths is comprised of 'folder' which is the path to the outbox, and 'chunk',
        which is the subpath of "title folder/chunk name"
        '''
        for folder, chunk in chunk_paths:
            missing = False
            frame_count_expected = 1
            frame_count_found = 0
            fSize = 0
            path = '/'.join([folder, chunk])

            try:
                ## if only file header found, consider missing
                fSize = os.path.getsize('/'.join([folder, chunk]))
                if fSize < (4 * 1024):
                    missing = True
                else:
                    ffprobeRet = subprocess.run(["ffprobe", 
                        "-v", 
                        "error", 
                        "-count_frames", 
                        "-select_streams", 
                        "v:0", 
                        "-show_entries", 
                        "stream=nb_read_frames", 
                        "-of", 
                        "default=nokey=1:noprint_wrappers=1", 
                        path], stdout=subprocess.PIPE)

                    frame_count_found = ffprobeRet.stdout.decode('utf-8')
                    frame_count_found = int(frame_count_found) - 1  ## off by one
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
                            frame_count_expected = int(chunkEnd.group(1)) - 1  ## off by one
                        except AttributeError as error:
                            missing = True
                            self.logger.warning("chunkStart, chunkEnd missing! :: {}".format(
                                str(error)))
                            self.logger.warning("chunk: {}".format(str(path)))
                            self.logger.warning("flagging chunk for requeue")
                            self.logger.debug("data : " + str(data.stdout.decode('utf-8')))
                            ## DEBUGGING REMOVE ME
                            shutil.copy2(path, self.outbox)
                    elif chunkEnd == None:
                        try:
                            frame_count_expected = int(totalFrames) - int(chunkStart.group(1))
                            self.logger.debug("total-frames: " + str(totalFrames))
                            self.logger.debug("chunk: " + str(path))
                        except AttributeError as error:
                            missing = True
                            self.logger.error("chunkStart, chunkEnd missing! Error: {}".format(
                                str(error)))
                            self.logger.error("chunk: " + str(path))
                    else:
                        frame_count_expected = int(chunkEnd.group(1)) - int(chunkStart.group(1))

                    self.logger.debug("Frames Found/Expected: {}/{} :: {}".format(
                        str(frame_count_found), str(frame_count_expected), str(path)))
            except FileNotFoundError:
                ## exception handling in desperate need of reworking
                self.logger.info("Missing chunk {}".format(str(chunk)))
                missing = True


            '''
            grab the file's number from its name, and use it to locate the
            correct index of the tuple (job_id, job) in jobHandles. push
            the tuple to redoJobs for reprocessing
            '''
            if missing or (int(frame_count_expected) != int(frame_count_found)):
                self.logger.info("Found failed job {}".format(str(chunk)))
                chunk_name = chunk.split('/')[1]

                #self.logger.debug("chunk_name: " + str(chunk_name))
                chunk_number = re.match('^(\d{3,7})_.*\.265', chunk_name)

                #self.logger.debug("chunk_number: " + str(chunk_number.group(1)))
                #self.logger.debug("job_handles len: " + str(len(job_handles)))
                offender = job_handles[int(chunk_number.group(1))]

                self.logger.debug("offender: " + str(offender))
                redo_jobs.append(offender)


        return_array[return_index] = redo_jobs



    def rebuildVideo(self, full_outbound_path: str) -> str:
        '''
        use mkvmerge tool to piece together the completed video from chunk files.
        function builds the command string, which ends up in the format:
            mkvmerge --output <outpath> chunk_1 + chunk_2 + chunk_N

        output file is named "noaudio_<video file name>"
        '''

        quiet = None
        dirList = os.listdir(full_outbound_path)
        dirFiles = [x for x in dirList if x[-4:] == '.265']
        dirFiles.sort()
        tempFileName = '_'.join(['noaudio', self._getFileName(self.target)])
        no_audio_file_path = '/'.join([full_outbound_path, tempFileName])
        if self.log_level == DEBUG:
            quiet = ''
        else:
            quiet = '--quiet'

        cmd = ['mkvmerge', quiet, '--output', r'"{}"'.format(no_audio_file_path)]

        firstFlag = 0
        for chunk in dirFiles:
            path = '/'.join([full_outbound_path, chunk])
            if firstFlag == 1:
                cmd.append('+')

            cmd.append(r'"{}"'.format(path))
            firstFlag = 1

        cmdString = ' '.join(cmd)
        self.logger.debug("rebuildVideo() cmd: {}".format(str(cmdString)))
        os.system(cmdString)

        return no_audio_file_path


    def mergeAudio(self, no_audio_file_path: str, full_outbound_path: str) -> str:
        '''
        use mkvmerge to recombine the newly merged video track with the original audio track
        from the source.
        '''

        quiet = None
        final_file_name = '/'.join([full_outbound_path, self._getFileName(self.target)])
        ## append a status indicator so users know the file is still being built
        tempFileName = final_file_name + '.processing'
        if self.log_level == DEBUG:
            quiet = ''
        else:
            quiet = '--quiet'

        cmd = ['mkvmerge', quiet, '--output', 
                r'"{}"'.format(tempFileName), 
                '-D', 
                r'"{}"'.format(self.target), 
                r'"{}"'.format(no_audio_file_path)]

        cmdString = ' '.join(cmd)
        self.logger.debug("mergeAudio() cmd: {}".format(str(cmdString)))
        os.system(cmdString)

        ## remove status indicator from filename
        os.rename(tempFileName, final_file_name)

        return final_file_name


    def cleanOutFolder(self, full_outbound_path: str, final_file_name: str) -> None:
        '''
        Verify that a new video file is in the destination folder, and if so
        delete all video chunks and surplus data
        '''

        video = Path(final_file_name)
        if not video.is_file():
            self.logger.error("Cannot find completed video file. Delaying cleaning")
        else:
            dir_files = os.listdir(full_outbound_path)
            for entry in dir_files:
                if entry[-4:] == '.265':
                    os.remove(''.join([full_outbound_path, '/', entry]))
                if entry[0:7] == 'noaudio':
                    os.remove(''.join([full_outbound_path, '/', entry]))



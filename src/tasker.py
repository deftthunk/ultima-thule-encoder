import re, os, math, sys
import shutil
import subprocess
import logging, logging.handlers
import threading
from collections import deque
from pathlib import Path
from time import sleep
from redis import Redis
from rq import Queue, Worker
from .task import Task


inbox = "/ute/inbox"
outbox = "/ute/outbox"
doneDir = "done"
highPriorityDir = "high"
logLevel = logging.DEBUG
config_jobTimeout = 300
config_cropSampleCount = 17
config_timeOffsetPercent = 0.15
config_frameBufferSize = 100
config_jobSize = 150
config_checkWorkThreadCount = 8
config_findWorkThreadCountMax = 4

## logging setup for aggregator container
rootLogger = logging.getLogger('ute')
rootLogger.setLevel(logLevel)
socketHandler = logging.handlers.SocketHandler('aggregator', 
      logging.handlers.DEFAULT_TCP_LOGGING_PORT)

rootLogger.addHandler(socketHandler)
taskerLogger = logging.getLogger('ute.tasker')


## delete all pending tasks in the Broker queue
def purgeQueue(q):
    q.empty()
    taskerLogger.info("Queued jobs purged")
    
    #return numDeleted


## kill all active tasks
def purgeActive(q):
    for job in q.jobs:
        if job.get_status() == 'started':
            job.cancel

    taskerLogger.info("All tasks purged")


'''
looks for work in NFS inbox file share. it detects folders by looking at the
length of the filepath. if the filepath is longer, it's a folder. if the folder
is not the "done directory" or "high priority directory", it is pushed onto
the queue.
'''
def findWork(workQLow, workQHigh, threadKeeper):
    newLowList = []
    newHighList = []
    activeFilesHigh = [name for (name, priority) in threadKeeper.keys() if priority == 'high']
    activeFilesLow = [name for (name, priority) in threadKeeper.keys() if priority == 'low']
    pathCount = len(inbox.split('/'))

    for (dirPath, dirNames, fileNames) in os.walk(inbox):
        pathArr = dirPath.split('/')
        if len(pathArr) == pathCount:
            newLowList.extend([os.path.join(dirPath, entry) for entry in fileNames])
            continue
        if len(pathArr) > pathCount:
            if pathArr[pathCount] == highPriorityDir:
                newHighList.extend([os.path.join(dirPath, entry) for entry in fileNames])
                continue
            elif pathArr[pathCount] != doneDir:
                newLowList.extend([os.path.join(dirPath, entry) for entry in fileNames])

    '''
    check for new files by finding the difference between the work queue 
    and the list of files we just made.
    '''
    newLowFiles = set(newLowList).symmetric_difference(set(workQLow))
    newHighFiles = set(newHighList).symmetric_difference(set(workQHigh))

    '''
    ensure that files currently being worked on (and therefore not found in
    the current queues) are not re-introduced to UTE. If not found in the
    threadKeeper keys list (activeFiles), then append the list of new files
    to the correct queue
    '''
    totalNewFiles = 0
    lowIntersect = set(newLowFiles).intersection(activeFilesLow)
    highIntersect = set(newHighFiles).intersection(activeFilesHigh)


    '''
    detect when a file is still in the process of being copied into the inbox.
    if so, ignore the file and we'll evaluate it again on the next pass.
    '''
    def checkFileTransferProgress(files):
        readyList = []
        threadArray = []
        sleepDuration = 3

        ## preallocate array so that we can access 
        for i in range(len(files)):
            readyList.append(i)


        ## file size delta function, defined to be made easy for multi-threading
        def watchFileSize(filePath, index):
            size1 = os.stat(f).st_size
            sleep(sleepDuration)
            size2 = os.stat(f).st_size

            if size2 == size1:
                readyList[index] = filePath


        '''
        to speed up checking if each file is changing size, we parallelize the process
        with threads. this is all I/O waiting, so we can have more threads than our
        CPU may support. default max is 30. if we hit max, we wait the allotted time 
        that the watchFileSize() function is waiting, which should mostly expire the
        existing threads.

        results are placed in a pre-allocated index in readyList
        for i, f in enumerate(files):
            thread = threading.Thread(target = watchFileSize, args = (f, i))
            thread.start()
            threadArray.append(thread)

            if (i % config_findWorkThreadCountMax == 0) and (i > 0):
                sleep(sleepDuration)


        ## join watchFileSize() threads
        sleep(sleepDuration)
        for i, _ in enumerate(threadArray):
            threadArray[i].join()
        '''

        for i, f in enumerate(files):
            watchFileSize(f, i)

        finalList = [i for i in readyList if type(i) == str]
        finalList.sort()
        taskerLogger.info("{} of {} files ready for queuing".format(
              str(len(finalList)), str(len(files))))
        return finalList


    '''
    look for new files by comparing the previous folder survey of inbox to the current state.
    '''
    if lowIntersect == set() and len(newLowFiles) > 0:
        taskerLogger.info("Low Priority file(s) found: " + str(len(newLowFiles)))
        #taskerLogger.debug("Low set intersection " + str(set(newLowFiles).intersection(activeFilesLow)))
        newLowFiles = checkFileTransferProgress(newLowFiles)
        workQLow.extend([entry for entry in newLowFiles])
        totalNewFiles += len(newLowFiles)
        taskerLogger.debug("workQLow: " + str(workQLow))

    if highIntersect == set() and len(newHighFiles) > 0:
        taskerLogger.info("High Priority file(s) found: " + str(len(newHighFiles)))
        #taskerLogger.debug("High set intersection " + str(set(newHighFiles).intersection(activeFilesHigh)))
        newHighFiles = checkFileTransferProgress(newHighFiles)
        workQHigh.extend([entry for entry in newHighFiles])
        totalNewFiles += len(newHighFiles)
        taskerLogger.debug("workQHigh: " + str(workQHigh))

    return totalNewFiles


'''
find clients using the broker container to get a count of 
available workers in the swarm
'''
def getClientCount(redisLink, workerList):
    workers = Worker.all(connection=redisLink)
    taskerLogger.debug("Found " + str(len(workers)) + " workers")

    if not len(workers) == len(workerList):
        change = set(workers).symmetric_difference(set(workerList))
        if len(workers) > len(workerList):
            taskerLogger.info("New worker(s): ")
            for worker in change:
                taskerLogger.info(str(worker.name))
        else:
            taskerLogger.info("Lost worker(s): ")
            for worker in change:
                taskerLogger.info(str(worker.name))

        taskerLogger.info("{} active workers".format(len(workers)))

    return len(workers), workers


'''
tbd
'''
def taskManager(q, redisLink, targetFile, taskWorkers, threadId, rqDummy):
    ## create and initialize new 'Task' object
    task = Task({ \
            'threadId' : threadId,
            'inbox' : inbox,
            'outbox' : outbox,
            'target' : targetFile,
            'logLevel' : logLevel,
            'doneDir' : doneDir,
            'jobTimeout' : config_jobTimeout,
            'cropSampleCount' : config_cropSampleCount,
            'timeOffsetPercent' : config_timeOffsetPercent,
            'frameBufferSize' : config_frameBufferSize,
            'jobSize' : config_jobSize,
            'workers' : taskWorkers })

    frameCountTotal, frameRate, duration = task.getFrameCount()
    encodeTasks, outboundFile, chunkPaths = task.buildCmdString(
            frameCountTotal, frameRate, duration)

    ## determine full path of folder for video chunks and create folder if not present
    fullOutboundPath = task.MakeOutboundFolderPath()
    taskerLogger.info("TID:{} Outbound path: {}".format(str(threadId), str(fullOutboundPath)))

    '''
    check first to see if we're resuming a task rather than starting a new one.
    if there's existing work already, CheckForPriorWork() will shorten the 
    'encodeTasks' list for PopulateQueue()
    '''
    encodeTasksReference, encodeTasksActual = task.CheckForPriorWork(encodeTasks,
            chunkPaths, taskWorkers)

    q, jobHandles = task.PopulateQueue(q, encodeTasksActual)

    '''
    if the task queues are different lengths, it means we are executing on
    prior work. But we still need to create all the job objects in case there is
    an error detected later. So, we perform a dummy version on a RQ instance that 
    no worker is subscribed to, and generate those job objects for CheckWork(). 
    Then delete everything in the dummy queue
    '''
    jobHandlesReference = None
    if len(encodeTasksReference) != len(encodeTasksActual):
        rqDummy, jobHandlesReference = task.PopulateQueue(rqDummy, encodeTasksReference)
        rqDummy.empty()
    else:
        jobHandlesReference = jobHandles


    
    tCount = config_checkWorkThreadCount
    ## how many chunks per CheckWork() thread
    checkSize = int(len(chunkPaths) / tCount)

    while True:
        taskerLogger.info("TID:{} Waiting on jobs".format(str(threadId)))
        task.WaitForTaskCompletion(q, jobHandles.copy())

        taskerLogger.info("TID:{} Checking work for errors".format(str(threadId)))
        threadArray = []
        jobHandlesTemp = []

        '''
        Once WaitForTaskCompletion has finished, initiate a check on the chunks of video to ensure
        there are no errors. This is done via CheckWork(). But because CheckWork() is highly I/O
        dependent in network scenarios, UTE speeds up this process by using multiple concurrent
        threads, and passing each CheckWork() thread a block of chunks to check.
        '''
        for t in range(tCount):
            ## prime jobHandlesTemp
            jobHandlesTemp.append([])

            ## (re)calculate range of chunk paths to send each thread
            start = t * checkSize
            end = ((t + 1) * checkSize) + 1   ## because a[i:j] only goes to j-1

            ## in case we come up short on math, make sure last thread gets the rest
            if t == tCount - 1:
                end = len(chunkPaths)

            taskerLogger.debug("TID:{} CheckWork() range start/end: {} / {}".format(str(threadId),
                str(start), str(end - 1)))

            '''
            Create a thread as many times as configured by the user. These threads run CheckWork() 
            concurrently, passing it a portion of the chunk list to verify, the job handle objects
            used to create those chunks, a reference to a temporary array to return their list of 
            chunks requiring requeing, and the index in which to place their array of failed chunks
            '''
            thread = threading.Thread(
                target = task.CheckWork,
                args = (chunkPaths[start:end], jobHandlesReference, jobHandlesTemp, t))

            taskerLogger.debug("TID:{} Starting CheckWork thread {}".format(str(threadId), str(t)))
            thread.start()
            threadArray.append(thread)


        ## wait for CheckWork() threads to finish 
        taskerLogger.info("waiting for check threads")
        for i, _ in enumerate(threadArray):
            threadArray[i].join()
            taskerLogger.debug("TID:{} CheckWork thread {} returned".format(str(threadId), str(i)))

        ## combine the failed chunks (if any) of all CheckWork() threads into jobHandles[]
        jobHandles = []
        debugCtr = 0
        for i, array in enumerate(jobHandlesTemp):
            jobHandles += array
            taskerLogger.debug("TID:{} CheckWork thread {} length: {}".format(str(threadId), 
                str(i), len(array)))
            debugCtr += 1

        '''
        if we have failed jobs, requeue them

        chunkPaths takes a tuple whos contents look like ('/ute/outbox', 'movie_folder/chunk_name')
        we have the first element from the outbox var, but the second one we'll dig out from the 
        job's argument string

        FYI, jobHandles is also an array of tuples
        '''
        if len(jobHandles) > 0:
            chunkPaths = []
            for jobId, job in jobHandles:
                task.RequeueJob(job.args, q, jobId)
                jobPath = re.search(r' -o .*?(/.*\.265)', str(job.args))
                subPath = jobPath.group(1).split(outbox)[-1][1:]
                chunkPaths.append((outbox, subPath))

                taskerLogger.debug("TID:{} jobPath: {}".format(str(threadId), str(jobPath.group(1))))
                taskerLogger.debug("TID:{} Requeuing {}".format(str(threadId), str(jobId)))

            taskerLogger.info("TID:{} Waiting for requeued jobs to complete".format(str(threadId)))

            '''
            recalculate some things for our requeue chunks. we don't want to re-check all
            chunks just to verify a small subset.
            '''
            if len(jobHandles) < tCount:
                tCount = 1

            taskerLogger.debug("TID:{} chunkPaths len {}".format(str(threadId), str(len(chunkPaths))))
            checkSize = int(len(chunkPaths) / tCount)
            continue
        else:
            break


    ## wrap things up. merge chunks, merge audio, handle source file, etc
    taskerLogger.info("TID:{} Building new Matroska file".format(str(threadId)))
    noAudioFile = task.RebuildVideo(fullOutboundPath)

    taskerLogger.info("TID:{} Merging audio tracks into new MKV file".format(str(threadId)))
    finalFileName = task.MergeAudio(noAudioFile, fullOutboundPath)

    sleep(1)
    taskerLogger.info("TID:{} Clearing out '.265' chunk files".format(str(threadId)))
    task.CleanOutFolder(fullOutboundPath, finalFileName)

    sleep(1)
    task.IndicateCompleted()
    taskerLogger.info("\nTID:{} Finished {}".format(str(threadId), str(targetFile)))



## do stuff
def main(): 
    ## note: two types of queues. one is a redis queue, the other is a python
    ## FIFO queue for organizing files
    redisLink = Redis('redis')
    workerList = []
    rqLow = Queue('low', connection=redisLink)
    rqHigh = Queue('high', connection=redisLink)
    rqDummy = Queue('dummy', connection=redisLink)
    workQLow = deque()
    workQHigh = deque()
    threadKeeper = {}
    threadId = 0
    highThreads, lowThreads = 0, 0

    ## check for available workers
    (workerCount, workerList) = getClientCount(redisLink, workerList)
    if workerCount == 0:
        taskerLogger.debug("No workers, sleeping")
        while getClientCount(redisLink, workerList)[0] == 0:
            sleep(30)

    while True:
        ## check for files
        fwReturn = findWork(workQLow, workQHigh, threadKeeper)
        if fwReturn == 0 and len(threadKeeper) == 0:
            taskerLogger.info("No files; UTE sleeping")
            while findWork(workQLow, workQHigh, threadKeeper) == 0:
                sleep(30)

        ## determine if we're pulling work from High or Low queue
        targetQueue = None
        targetFile = None
        newThread = False
        highPriority = False

        if highThreads < 2 and len(workQHigh) > 0:
            try:
                taskerLogger.debug("Queing high priority work")
                targetFile = workQHigh.popleft()
                targetQueue = rqHigh
                newThread = True
                highPriority = True
                highThreads += 1

                '''
                if there's already a thread, delay creation of a second thread to
                avoid interleaving thread2 jobs with thread1 jobs, since it takes
                a few seconds to push jobs to Redis
                '''
                if highThreads == 1:
                    sleep(30)
            except IndexError:
                taskerLogger.error("Unable to find files in High Queue")

        elif lowThreads < 2 and len(workQLow) > 0:
            try:
                taskerLogger.debug("Queing low priority work")
                targetFile = workQLow.popleft()
                targetQueue = rqLow
                newThread = True
                lowThreads += 1

                '''
                if there's already a thread, delay creation of a second thread to
                avoid interleaving thread2 jobs with thread1 jobs, since it takes
                a few seconds to push jobs to Redis
                '''
                if lowThreads == 1:
                    sleep(10)
            except IndexError:
                taskerLogger.error("Unable to find files in Low Queue")

        '''
        if it was determined above that a new thread should be created,
        make it and start it, adding the thread object to threadKeeper
        '''
        if newThread:
            ## create a numerical id to differentiate threads in log output
            threadId += 1
            if threadId > 9999:
                threadId = 1

            thread = threading.Thread(
                target = taskManager, 
                args = (targetQueue, redisLink, targetFile, workerCount, threadId, rqDummy))

            taskerLogger.debug("Starting thread")
            thread.start()
            if highPriority:
                threadKeeper[(targetFile, 'high')] = thread
            else:
                threadKeeper[(targetFile, 'low')] = thread

        ## join finished threads 
        delThreadItem = None
        if len(threadKeeper) > 0:
            for (name, priority), thread in threadKeeper.items():
                if not thread.is_alive():
                    taskerLogger.debug("Joining thread " + name)
                    thread.join()
                    delThreadItem = (name, priority)
                    if priority == 'high':
                        highThreads -= 1
                    else:
                        lowThreads -= 1

        if not delThreadItem == None:
            del threadKeeper[delThreadItem]

        #taskerLogger.debug("End loop, sleep 10")
        sleep(15)



if __name__ == "__main__":
    main()





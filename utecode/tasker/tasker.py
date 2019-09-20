import re, os, math, sys
import shutil
import subprocess
import logging
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
debug = False
config_jobTimeout = 480
config_cropSampleCount = 13
config_timeOffsetPercent = 0.15
config_frameBufferSize = 100
config_jobSize = 300

#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.basicConfig(level=logging.DEBUG)


## delete all pending tasks in the Broker queue
def purgeQueue(q):
    q.empty()
    logging.info("Queued jobs purged")
    
    #return numDeleted


## kill all active tasks
def purgeActive(q):
    for job in q.jobs:
        if job.get_status() == 'started':
            job.cancel

    logging.info("All tasks purged")


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

    ## check for new files by finding the difference between the work queue 
    ## and the list of files we just made.
    newLowFiles = set(newLowList).symmetric_difference(set(workQLow))
    newHighFiles = set(newHighList).symmetric_difference(set(workQHigh))

    ## ensure that files currently being worked on (and therefore not found in
    ## the current queues) are not re-introduced to UTE. If not found in the
    ## threadKeeper keys list (activeFiles), then append the list of new files
    ## to the correct queue
    totalNewFiles = 0
    lowIntersect = set(newLowFiles).intersection(activeFilesLow)
    highIntersect = set(newHighFiles).intersection(activeFilesHigh)

    if lowIntersect == set() and len(newLowFiles) > 0:
        logging.info("Low Priority file(s) found: " + str(len(newLowFiles)))
        logging.debug("Low set intersection " + str(set(newLowFiles).intersection(activeFilesLow)))
        workQLow.extend([entry for entry in newLowFiles])
        totalNewFiles += len(newLowFiles)
        logging.debug("workQLow: " + str(workQLow))

    if highIntersect == set() and len(newHighFiles) > 0:
        logging.info("High Priority file(s) found: " + str(len(newHighFiles)))
        logging.debug("High set intersection " + str(set(newHighFiles).intersection(activeFilesHigh)))
        workQHigh.extend([entry for entry in newHighFiles])
        totalNewFiles += len(newHighFiles)
        logging.debug("workQHigh: " + str(workQHigh))

    return totalNewFiles


'''
find clients using the broker container to get a count of 
available workers in the swarm
'''
def getClientCount(redisLink, workerList):
    workers = Worker.all(connection=redisLink)
    logging.debug("Found " + str(len(workers)) + " workers")

    if not len(workers) == len(workerList):
        change = set(workers).symmetric_difference(set(workerList))
        if len(workers) > len(workerList):
            logging.info("New worker(s): ")
            for worker in change:
                logging.info(str(worker.name))
        else:
            logging.info("Lost worker(s): ")
            for worker in change:
                logging.info(str(worker.name))

        logging.info("{} active workers".format(len(workers)))

    return len(workers), workers


'''
tbd
'''
def taskManager(q, redisLink, targetFile, taskWorkers, threadId):
    ## create and initialize new 'Task' object
    task = Task({ \
            'threadId' : threadId,
            'inbox' : inbox,
            'outbox' : outbox,
            'target' : targetFile,
            'debug' : debug,
            'doneDir' : doneDir,
            'jobTimeout' : config_jobTimeout,
            'cropSampleCount' : config_cropSampleCount,
            'timeOffsetPercent' : config_timeOffsetPercent,
            'frameBufferSize' : config_frameBufferSize,
            'jobSize' : config_jobSize,
            'workers' : taskWorkers })

    frameCountTotal, frameRate, duration = task.getFrameCount()
    encodeTasks, outboundFile, chunkPaths = task.buildCmdString( \
            frameCountTotal, frameRate, duration)

    ## determine full path of folder for video chunks
    tempPath = outboundFile.split('/')
    tempLen = len(tempPath)
    tempPath2 = '/'.join(tempPath[:tempLen-1])
    fullOutboundPath = '/'.join([outbox, tempPath2])
    logging.info("TID:{} Outbound path: {}".format(str(threadId), str(fullOutboundPath)))
    q, jobHandles = task.populateQueue(q, encodeTasks)

    while True:
        logging.info("TID:{} Waiting on jobs".format(str(threadId)))
        task.waitForTaskCompletion(q, jobHandles.copy())

        sleep(7)

        logging.info("TID:{} Checking work for errors".format(str(threadId)))
        jobHandles = task.CheckForErrors(chunkPaths, jobHandles)

        if len(jobHandles) > 0:
            for jobId, job in jobHandles:
                task.RequeueJob(job.args, q, jobId)
        else:
            break


    logging.info("TID:{} Building new Matroska file".format(str(threadId)))
    noAudioFile = task.rebuildVideo(fullOutboundPath)

    logging.info("TID:{} Merging audio tracks into new MKV file".format(str(threadId)))
    finalFileName = task.mergeAudio(noAudioFile, fullOutboundPath)

    sleep(1)
    logging.info("TID:{} Clearing out '.265' chunk files".format(str(threadId)))
    task.cleanOutFolder(fullOutboundPath, finalFileName)

    sleep(1)
    task.indicateCompleted()
    logging.info("\nTID:{} Finished {}".format(str(threadId), str(targetFile)))



## do stuff
def main(): 
    ## note: two types of queues. one is a redis queue, the other is a python
    ## FIFO queue for organizing files
    redisLink = Redis('redis')
    workerList = []
    rqLow = Queue('low', connection=redisLink)
    rqHigh = Queue('high', connection=redisLink)
    workQLow = deque()
    workQHigh = deque()
    threadKeeper = {}
    threadId = 0
    highThreads, lowThreads = 0, 0

    ## check for available workers
    (workerCount, workerList) = getClientCount(redisLink, workerList)
    if workerCount == 0:
        logging.debug("No workers, sleeping")
        while getClientCount(redisLink, workerList)[0] == 0:
            sleep(30)

    while True:
        ## check for files
        fwReturn = findWork(workQLow, workQHigh, threadKeeper)
        if fwReturn == 0 and len(threadKeeper) == 0:
            logging.info("No files; UTE sleeping")
            while findWork(workQLow, workQHigh, threadKeeper) == 0:
                sleep(30)

        ## determine if we're pulling work from High or Low queue
        targetQueue = None
        targetFile = None
        newThread = False
        highPriority = False

        if highThreads < 2 and len(workQHigh) > 0:
            try:
                logging.debug("Queing high priority work")
                targetFile = workQHigh.popleft()
                targetQueue = rqHigh
                newThread = True
                highPriority = True
                highThreads += 1
            except IndexError:
                logging.error("Unable to find files in High Queue")

        elif lowThreads < 2 and len(workQLow) > 0:
            try:
                logging.debug("Queing low priority work")
                targetFile = workQLow.popleft()
                targetQueue = rqLow
                newThread = True
                lowThreads += 1
            except IndexError:
                logging.error("Unable to find files in Low Queue")

        '''
        if it was determined above that a new thread should be created,
        make it and start it, adding the thread object to threadKeeper
        '''
        if newThread:
            ## create a numerical id to differentiate threads in log output
            threadId += 1
            if threadId > 9999:
                threadId = 1

            thread = threading.Thread( \
                target = taskManager, \
                args = (targetQueue, redisLink, targetFile, workerCount, \
                threadId))

            logging.debug("Starting thread")
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
                    logging.debug("Joining thread " + name)
                    thread.join()
                    delThreadItem = (name, priority)
                    if priority == 'high':
                        highThreads -= 1
                    else:
                        lowThreads -= 1

        if not delThreadItem == None:
            del threadKeeper[delThreadItem]

        #logging.debug("End loop, sleep 10")
        sleep(15)



if __name__ == "__main__":
    main()





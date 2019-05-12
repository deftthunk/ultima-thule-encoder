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
debug = False
highPriorityDir = "high"
config_jobTimeout = 600
config_cropSampleCount = 13
config_timeOffsetPercent = 0.15
task_workers = 0

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
    if set(newLowFiles).intersection(activeFilesLow) == set() and len(newLowFiles) > 0:
        logging.debug("Files found: " + str(len(newLowFiles)))
        workQLow.extend([entry for entry in newLowFiles])
    if set(newHighFiles).intersection(activeFilesHigh) == set() and len(newHighFiles) > 0:
        logging.debug("High Priority files found: " + str(len(newHighFiles)))
        workQHigh.extend([entry for entry in newHighFiles])

    return len(newLowFiles) + len(newHighFiles)


'''
find clients using the broker container to get a count of 
available workers in the swarm
'''
def getClientCount(redisLink):
    workers = Worker.all(connection=redisLink)
    #logging.info("Found " + str(len(workers)) + " workers")

    #for worker in workers:
    #    logging.info(str(worker.name))

    return len(workers)




def taskManager(q, redisLink, targetFile):
    task_workers = getClientCount(redisLink)

    ## create and initialize new 'Task' object
    task = Task({ \
            'inbox' : inbox,
            'outbox' : outbox,
            'target' : targetFile,
            'debug' : debug,
            'doneDir' : doneDir,
            'jobTimeout' : config_jobTimeout,
            'cropSampleCount' : config_cropSampleCount,
            'timeOffsetPercent' : config_timeOffsetPercent,
            'workers' : task_workers })


    frameCountTotal, frameRate, duration = task.getFrameCount()
    encodeTasks, outboundFile = task.buildCmdString( \
            frameCountTotal, frameRate, duration)

    ## determine full path of folder for video chunks
    tempPath = outboundFile.split('/')
    tempLen = len(tempPath)
    tempPath2 = '/'.join(tempPath[:tempLen-1])
    fullOutboundPath = '/'.join([outbox, tempPath2])
    logging.info("Outbound path: " + str(fullOutboundPath))

    q, jobHandles = task.populateQueue(q, encodeTasks)

    logging.info("Waiting on jobs...")
    task.waitForTaskCompletion(q, jobHandles)

    logging.info("Building new Matroska file")
    noAudioFile = task.rebuildVideo(fullOutboundPath)

    logging.info("Merging audio tracks into new MKV file")
    finalFileName = task.mergeAudio(noAudioFile, fullOutboundPath)

    sleep(1)
    logging.info("Clearing out '.265' chunk files")
    task.cleanOutFolder(fullOutboundPath, finalFileName)

    sleep(1)
    task.indicateCompleted()
    logging.info("\nFinished " + targetFile)




## do stuff
def main(): 
    ## note: two types of queues. one is a redis queue, the other is a python
    ## FIFO queue for organizing files
    redisLink = Redis('redis')
    rqLow = Queue('low', connection=redisLink)
    rqHigh = Queue('high', connection=redisLink)
    workQLow = deque()
    workQHigh = deque()
    threadKeeper = {}
    highThreads, lowThreads = 0, 0

    while True:
        ## check for files and clients
        if findWork(workQLow, workQHigh, threadKeeper) == 0 and \
                threading.active_count() < 2:
            logging.info("No files; UTE sleeping")
            while findWork(workQLow, workQHigh, threadKeeper) == 0:
                sleep(30)
                continue

        ## are there workers available
        if getClientCount(redisLink) == 0:
            logging.debug("No workers, sleeping")
            while getClientCount(redisLink) == 0:
                sleep(30)
                continue


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
                continue
        elif lowThreads < 2 and len(workQLow) > 0:
            try:
                logging.debug("Queing low priority work")
                targetFile = workQLow.popleft()
                targetQueue = rqLow
                newThread = True
                lowThreads += 1
            except IndexError:
                logging.error("Unable to find files in Low Queue")
                continue

        ## if it was determined above that a new thread should be created,
        ## make it and start it, adding the thread object to threadKeeper
        if newThread:
            thread = threading.Thread( \
                target = taskManager, \
                args = (targetQueue, redisLink, targetFile))

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
        sleep(10)



if __name__ == "__main__":
    main()





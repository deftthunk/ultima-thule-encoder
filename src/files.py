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
detect when a file is still in the process of being copied into the inbox.
if so, ignore the file and we'll evaluate it again on the next pass.
'''
def CheckFileTransferProgress(files, taskerLogger):
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
looks for work in NFS inbox file share. it detects folders by looking at the
length of the filepath. if the filepath is longer, it's a folder. if the folder
is not the "done directory" or "high priority directory", it is pushed onto
the queue.
'''
def GetNewFiles(workQLow, workQHigh, threadKeeper, taskerLogger):
    newLowList = []
    newHighList = []
    activeFilesHigh = [name for (name, priority) in threadKeeper.keys() if priority == 'high']
    activeFilesLow = [name for (name, priority) in threadKeeper.keys() if priority == 'low']
    pathCount = len(inbox.split('/'))

    for (dirPath, dirNames, fileNames) in os.walk(inbox):
        pathArr = dirPath.split('/')
        ## low priority files
        if len(pathArr) == pathCount:
            newLowList.extend([os.path.join(dirPath, entry) for entry in fileNames])
            continue
        ## high priority files, folders, etc
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

    return totalNewFiles, newLowFiles, newHighFiles



#def PreProcess(taskerLogger):
    


def FindWork(workQLow, workQHigh, threadKeeper, taskerLogger):
    newFileCount, newLowFiles, newHighFiles = GetNewFiles(workQLow, workQHigh, threadKeeper, taskerLogger)
#    if newFileCount > 0:
#        PreProcess(newLowFiles, newHighFiles, taskerLogger)


    return newFileCount
















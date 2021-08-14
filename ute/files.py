import re, os, math, sys
import shutil
import subprocess
import logging.handlers
import threading
from logging import Logger
from typing import List, Dict
from collections import deque
from statistics import mode, StatisticsError
from pathlib import Path
from time import sleep



class TaskItem:
    '''
    A TaskItem represents the video file and all accompanying data surrounding it.
    While this will always be about the video file, it may also include custom UTE 
    encoder settings, and other supporting files such as subtitles, audio, etc.
    '''
    def __init__(self, config, video_file_path, queue_priority, logger, lsmash_path=None):
        self.config = config
        self.video_file_path = video_file_path
        self.queue_priority = queue_priority
        self.logger = logger
        self.lsmash_path = lsmash_path


    def get_file_name(self, path=None):
        if path == None:
            return os.path.basename(self.video_file_path)
        else:
            return os.path.basename(path)


    def push_to_queue(self, queue):
        '''
        Add new item to a queue
        '''
        queue.extend(self.video_file_path)
        return len(queue)


    def get_frame_count(self, method='default'):
        '''
        Find the number of frames in the video. this can be error prone, so multiple
        methods are attempted
        '''
        frameCount, frameRate, duration = None, None, None
        mediaRet, mediaMatch, mediaFrameRate, mediaDuration = None, None, None, None

        if method == 'default' or method == 'mediainfo':
            ## attempting mediainfo method
            mediaRet = subprocess.run(["mediainfo", "--fullscan", self.video_file_path], 
                    stdout=subprocess.PIPE)
            mediaMatch = re.search(r'Frame count.*?(\d+)', mediaRet.stdout.decode('utf-8'))
            mediaFrameRate = re.search(r'Frame rate.*?(\d\d\d?)(\.\d\d?\d?)?', 
                    mediaRet.stdout.decode('utf-8'))
            mediaDuration = re.search(r'Duration.*?\s(\d{2,})\s*\n', 
                    mediaRet.stdout.decode('utf-8'))

        ## get frame rate
        if mediaFrameRate.group(2):
            frameRate = mediaFrameRate.group(1) + mediaFrameRate.group(2)
        elif mediaFrameRate.group(1):
            frameRate = mediaFrameRate.group(1)
        else:
            self.logger.warning("Unable to find frame rate!")

        ## get time duration
        duration = mediaDuration.group(1)

        '''
        if we cant find a frame count, we'll do it the hard way and count frames
        using ffprobe. this can end up being the case if the MKV stream is
        variable frame rate
        '''
        if mediaMatch == None or method == 'ffprobe':
            self.logger.info("Using ffprobe")
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
                self.video_file_path], stdout=subprocess.PIPE)

            frameCount = ffprobeRet.stdout.decode('utf-8')
        else:
            frameCount = mediaMatch.group(1)

        self.logger.info("Frame Count: {}".format(str(frameCount)))
        self.logger.info("Frame Rate: {}".format(str(frameRate)))
        self.logger.info("Duration: {}".format(str(duration)))
        self.logger.info("Calc FC: {}".format(str(float(duration) * float(frameRate) / 1000)))

        return int(frameCount), float(frameRate), int(duration)



    def detect_cropping(self, duration):
        '''
        Use ffmpeg to detect video cropping in target sample function attempts to do a 
        sampling across the file by avoiding the beginning and end (intro/credits) and 
        picks candidates distributed across the body where ffmpeg will have a better 
        chance of detecting cropped frames, then looks for a majority consensus among 
        the samples
        '''
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
            cmdRet = subprocess.run(["ffmpeg", 
                            "-ss", str(offset), 
                            "-i", self.video_file_path, 
                            "-t", "10", 
                            "-vf", "cropdetect=24:16:0", 
                            "-preset", "ultrafast", 
                            "-f", "null", 
                            "-"], 
                        stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
            match = re.search(r'\s(crop=\d+\:\d+[^a-zA-Z]*?)\n', 
                cmdRet.stdout.decode('utf-8'))
        
            if match:
                cropValues.append(match.group(1))
                self.logger.debug("Found crop value: {}".format(match.group(1)))

        ## parse results
        if len(cropValues) == 0:
            self.logger.info("No cropping detected")
            return ''
        elif len(cropValues) < self.cropSampleCount:
            self.logger.info("Cropping failed to get full sampling!")
            self.logger.info("Proceeding with cropping disabled")
            return ''
        else:
            try:
                result = mode(cropValues)
                self.logger.info("Cropping consensus: {}".format(str(result)))
                return result
            except StatisticsError:
                self.logger.info("No consensus found for cropping")
                self.logger.info("Try increasing sampling value")
                self.logger.info("Proceeding with cropping disabled")
                return ''


    def get_position(self, pos, fps):                                                           
        '''                                                                                      
        Calculate the position in the video (timewise) based on the frame we're                  
        looking for and how many frames per second it runs at                                    
        '''
        newPos = round(pos / fps, 2)
        return newPos


    def get_outbound_folder_path(self):
        '''
        Return the name of this file's outbox folder, and make it if it doesn't
        yet exist in outbox
        '''
        newFolder = self._getFileName(self.video_file_path)
        newPath = '/'.join([self.outbox, newFolder])
        ## make it if it doesn't exist
        try:
            os.makedirs(newPath, 0o777, exist_ok=True)
        except(FileNotFoundError) as error:
            self.logger.info("Unable to create 'outbox' folder or subfolder")
            self.logger.error("Error: {}".format(str(error)))
            return False

        return newPath


    def completed(self):
        '''
        Creates a "done" directory for both inbox and outbox, and moves the completed video files 
        into the folder.
        '''
        ## check if completed folder is present for inbox
        doneInbox = '/'.join([self.inbox, self.doneDir])
        os.makedirs(doneInbox, 0o777, exist_ok=True)
        ## check if completed folder is present for outbox
        doneOutbox = '/'.join([self.outbox, self.doneDir])
        os.makedirs(doneOutbox, 0o777, exist_ok=True)

        ## move source file to 'done' folder in inbox
        moveTargetInboxPath = '/'.join([doneInbox, self._getFileName(self.video_file_path)])
        self.logger.debug("file move: {}".format(str(moveTargetInboxPath)))
        shutil.move(self.video_file_path, moveTargetInboxPath)
        ## move finished file(s) to 'done' folder in outbox
        targetFolder = self.GetOutboundFolderPath.split('/')[-1]
        moveTargetOutboxPath = '/'.join([doneOutbox, targetFolder])
        self.logger.debug("folder move: {}".format(str(moveTargetOutboxPath)))
        shutil.move(completedFolder, moveTargetOutboxPath)


#    def IndicateCompleted(self, completedFolder):












def _check_file_transfer_progress(files, logger):
    '''
    Detect when a file is still in the process of being copied into the inbox.
    if so, ignore the file and we'll evaluate it again on the next pass.
    '''
    readyList = []
    thread_array = []
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
    To speed up checking if each file is changing size, we parallelize the process
    with threads. this is all I/O waiting, so we can have more threads than our
    CPU may support. default max is 30. if we hit max, we wait the allotted time 
    that the watchFileSize() function is waiting, which should mostly expire the
    existing threads.

    results are placed in a pre-allocated index in readyList
    for i, f in enumerate(files):
        thread = threading.Thread(target = watchFileSize, args = (f, i))
        thread.start()
        thread_array.append(thread)

        if (i % config_findWorkThreadCountMax == 0) and (i > 0):
            sleep(sleepDuration)


    ## join watchFileSize() threads
    sleep(sleepDuration)
    for i, _ in enumerate(thread_array):
        thread_array[i].join()
    '''

    for i, f in enumerate(files):
        watchFileSize(f, i)

    finalList = [i for i in readyList if type(i) == str]
    finalList.sort()
    logger.info("{} of {} files ready for queuing".format(
          str(len(finalList)), str(len(files))))
    return finalList


def detect_file_type(file_path):



def _get_new_files(work_queues, threadKeeper, logger, config):
    '''
    GetNewFiles() looks for work in NFS inbox file share. it detects folders by looking at the
    length of the filepath. if the filepath is longer, it's a folder. if the folder
    is not the "done directory" or "high priority directory", it is pushed onto
    the queue.
    '''
    new_lists = {'low':[], 'high':[]}
    activeFilesHigh = [name for (name, priority) in threadKeeper.keys() if priority == 'high']
    activeFilesLow = [name for (name, priority) in threadKeeper.keys() if priority == 'low']
    pathCount = len(config['inbox_path'].split('/'))
    iter_flag = False

    '''
    on first pass, attempt to prevent walking the 'completed files' folder, which could contain 
    a lot of data and slow us down. Also check to ensure it exists, and if not, create it.
    '''
    for (abs_dir_path, dir_names, file_names) in os.walk(config['inbox_path']):
        if not iter_flag:
            iter_flag = True
            try:
                dir_names.remove(config['completed_folder_name'])
            except ValueError:
                cfn_path = os.path.join(config['inbox_path'], config['completed_folder_name'])
                logger.info("Missing completion folder " + str(cfn_path) + ". Creating it now.")
                pass

        abs_dir_path_split = abs_dir_path.split('/')
        print("\n===rotation===")
        ## low priority files
        if len(abs_dir_path_split) == pathCount:
            print("LOW")
            print("abs_dir_path: " + abs_dir_path)
            print("dir_names: ", [x for x in dir_names])
            print("file_names: ", [x for x in file_names])
            new_lists['low'].extend([os.path.join(abs_dir_path, entry) for entry in file_names])
            continue
        ## high priority files, folders, etc
        elif abs_dir_path_split[pathCount] == config['high_priority_folder_name']:
            print("HIGH")
            print("abs_dir_path: " + abs_dir_path)
            print("dir_names: ", [x for x in dir_names])
            print("file_names: ", [x for x in file_names])
            new_lists['high'].extend([os.path.join(abs_dir_path, entry) for entry in file_names])
            continue
        ## nested folder trees in high/low
        elif abs_dir_path_split[pathCount] != config['completed_folder_name']:
            '''
            we've detected directory structures in the inbox or 'high' folder, but are not sure if
            they're high or low priority. Here we'll look for the 'high' folder in the path, and 
            confirm that it immediate follows the inbox path as described by the user in the config
            file. Failing that, we'll assume we're dealing with "low" priority work 
            '''
            priority = None
            try:
                temp_index = abs_dir_path_split.index(config['high_priority_folder_name'])
                temp_inbox_path = '/'.join(abs_dir_path_split[:temp_index])
                if temp_inbox_path == config['inbox_path']:
                    priority = 'high'
                else:
                    priority = 'low'
            except ValueError:
                priority = 'low'
                pass

            print("abs_dir_path: " + abs_dir_path)
            print("dir_names: ", [x for x in dir_names])
            print("file_names: ", [x for x in file_names])
            new_lists[priority].extend([os.path.join(abs_dir_path, entry) for entry in file_names])
        else:
            untracked_paths = [os.path.join(abs_dir_path, entry) for entry in file_names]
            for odd_path in untracked_paths:
                log_str = "File path is outside UTE's inbox management area: " + str(odd_path)
                logger.warning(str(log_str))


    config_paths = {}
    for priority, array in new_lists.items():
        file_type = detectFileType(f)
        if file_type == 


def return_new_files(new_lists, work_queues, activeFiles)
    '''
    check for new files by finding the difference between the work queue 
    and the list of files we just made.
    '''
    newLowFiles = set(newLowList).symmetric_difference(set(work_queues['low']))
    newHighFiles = set(newHighList).symmetric_difference(set(work_queues['high']))

    '''
    ensure that files currently being worked on (and therefore not found in
    the current queues) are not re-introduced to UTE. If not found in the
    threadKeeper keys list (activeFiles), then append the list of new files
    to the correct queue
    '''
    lowIntersect = set(newLowFiles).intersection(activeFilesLow)
    highIntersect = set(newHighFiles).intersection(activeFilesHigh)

    '''
    look for new files by comparing the previous folder survey of inbox to the current state.
    '''
    if lowIntersect == set() and len(newLowFiles) > 0:
        logger.info("Low Priority file(s) found: " + str(len(newLowFiles)))
#        newLowFiles = _check_file_transfer_progress(newLowFiles)
        logger.debug("Internal Queue (low): " + str(work_queues['low']))

    if highIntersect == set() and len(newHighFiles) > 0:
        logger.info("High Priority file(s) found: " + str(len(newHighFiles)))
#        newHighFiles = _check_file_transfer_progress(newHighFiles)
        logger.debug("Internal Queue (high): " + str(work_queues['high']))

    return newLowFiles, newHighFiles



#def pre_process(logger):
#    
#
#

'''
I need to create a way to reliably identify and apply new configurations at each folder level to 
the relevant task items.
- find appropriate config settings
- identify related files/folders attached to video file
- track said files correctly until completion of work, and also temporary files between reboots
- identify and move relevant files to 'done' folder in both INBOX and OUTBOX (certain copies?), while
    being sure to not move any shared files (ie. configs) that might be used by other files
- add ability to have configuration files persist in whatever folder/hierarchy found after work completes

'''


def find_work(work_queues: Dict, thread_keeper: Dict, logger: Logger, config) -> List[TaskItem]:
    task_items = []
    new_low_files, new_high_files = _get_new_files(
            work_queues, thread_keeper, logger, config
        )

    if len(new_low_files) > 0:
        for item_path in new_low_files:
            new_item = TaskItem(config, item_path, "low", logger)
            new_item.PushToQueue(work_queues['low'])
            task_items.append(new_item)

        logger.info("Low Priority Files Added: " + str(len(new_low_files)))

    if len(new_high_files) > 0:
        for item_path in new_high_files:
            new_item = TaskItem(config, item_path, "high", logger)
            new_item.PushToQueue(work_queues['high'])
            task_items.append(new_item)

        logger.info("High Priority Files Added: " + str(len(new_high_files)))

    logger.debug("workQLow: " + str(work_queues['low']))
    logger.debug("workQHigh: " + str(work_queues['high']))


#    if newFileCount > 0:
#        PreProcess(new_low_files, new_high_files, logger)

    return task_items










if __name__ == "__main__":
    import logging
    import ute.config
    from collections import deque

    threadKeeper = {}
    workQLow = deque()
    workQHigh = deque()
    workQPre = deque()
    logger = logging.getLogger()

















import re
import threading
from logging import getLogger
from time import sleep
from redis import Redis
from rq import Queue, Job

import ute.files
import ute.config
from ute.task import Task
from ute.files import TaskItem

class TaskManager:
    def __init__(self, 
                target_queue: Queue, 
                redis_link: Redis, 
                task_item: TaskItem, 
                worker_count: int, 
                thread_id: int, 
                dummy_queue: Queue) -> None:

        self.target_queue = target_queue
        self.redis_link = redis_link
        self.task_item = task_item
        self.worker_count = worker_count
        self.thread_id = thread_id
        self.dummy_queue = dummy_queue
        self.logger = getLogger("ute.manager-" + str(self.thread_id))



    #def Start(self):
    #    if self.task_item.






    def taskRun(self):
        ## create and initialize new 'Task' object
        task = Task({ \
                'thread_id' : thread_id,
                'inbox' : inbox,
                'outbox' : outbox,
                'vsScripts_path' : vsScripts,
                'target' : task_item,
                'logLevel' : logLevel,
                'doneDir' : doneDir,
            # 'jobTimeout' : config_jobTimeout,
            # 'cropSampleCount' : config_cropSampleCount,
            # 'timeOffsetPercent' : config_timeOffsetPercent,
            # 'frameBufferSize' : config_frameBufferSize,
            # 'vapoursynthEnabled' : config_vapoursynthEnabled,
            # 'vsScriptName' : config_vsScriptName,
            # 'jobSize' : config_jobSize,
                'worker_count' : self.worker_count })

        frame_count_total, frame_rate, duration = ute.files.get_frame_count()
        encodeTasksReference, outboundFile, chunk_paths = task.build_cmd_string(
                frame_count_total, frame_rate, duration)
        ## determine full path of folder for video chunks and create folder if not present
        full_outbound_path = ute.files.get_outbound_folder_path()
        self.logger.info("TID:{} Outbound path: {}".format(str(self.thread_id), str(full_outbound_path)))

        '''
        check first to see if we're resuming a task rather than starting a new one.
        if there's existing work already, CheckForPriorWork() will shorten the 
        'encodeTasks' list for PopulateQueue()
        '''
        encoding_start_index = task.check_for_prior_work(chunk_paths, self.worker_count)
        encodeTasksActual = encodeTasksReference[encoding_start_index:]

        #q, jobHandles = task.populate_queue(self.target_queue, encodeTasksActual)
        jobHandles = task.populate_queue(self.target_queue, encodeTasksActual)

        '''
        if the task queues are different lengths, it means we are executing on
        prior work. But we still need to create all the job objects in case there is
        an error detected later. So, we perform a dummy version on a RQ instance that 
        no worker is subscribed to, and generate those job objects for CheckWork(). 
        Then delete everything in the dummy queue
        '''
        job_handles_reference = None
        if len(encodeTasksReference) != len(encodeTasksActual):
            #rq_dummy, jobHandlesReference = task.populate_queue(rq_dummy, encodeTasksReference)
            #rq_dummy.empty()
            job_handles_reference = task.populate_queue(self.dummy_queue, encodeTasksReference)
            self.dummy_queue.empty()

        else:
            job_handles_reference = jobHandles

        
        tCount = config_checkWorkThreadCount
        ## how many chunks per CheckWork() thread
        checkSize = int(len(chunk_paths) / tCount)
        ## if the same chunk(s) keeps failing, track it to prevent an infinite fail
        failCount = 0
        retainChunks = False

        while True:
            self.logger.info("TID:{} Waiting on jobs".format(str(self.thread_id)))
            task.wait_for_task_completion(self.target_queue, jobHandles.copy())

            self.logger.info("TID:{} Checking work for errors".format(str(self.thread_id)))
            threadArray = []
            job_handles_temp = []

            '''
            Once WaitForTaskCompletion has finished, initiate a check on the chunks of video to ensure
            there are no errors. This is done via CheckWork(). But because CheckWork() is highly I/O
            dependent in network scenarios, UTE speeds up this process by using multiple concurrent
            threads, and passing each CheckWork() thread a block of chunks to check.
            '''
            for t in range(tCount):
                ## prime jobHandlesTemp
                job_handles_temp.append([])

                ## (re)calculate range of chunk paths to send each thread
                start = t * checkSize
                end = ((t + 1) * checkSize) + 1   ## because a[i:j] only goes to j-1

                ## in case we come up short on math, make sure last thread gets the rest
                if t == tCount - 1:
                    end = len(chunk_paths)

                self.logger.debug("TID:{} CheckWork() range start/end: {} / {}".format(str(self.thread_id),
                    str(start), str(end - 1)))

                '''
                Create a thread as many times as configured by the user. These threads run CheckWork() 
                concurrently, passing it a portion of the chunk list to verify, the job handle objects
                used to create those chunks, a reference to a temporary array to return their list of 
                chunks requiring requeing, and the index in which to place their array of failed chunks
                '''
                thread = threading.Thread(
                    target = task.check_work,
                        args = (chunk_paths[start:end], job_handles_reference, job_handles_temp, t)
                )

                self.logger.debug("TID:{} Starting CheckWork thread {}".format(str(self.thread_id), str(t)))
                thread.start()
                threadArray.append(thread)


            ## wait for CheckWork() threads to finish 
            self.logger.info("waiting for check threads")
            for i, _ in enumerate(threadArray):
                threadArray[i].join()
                self.logger.debug("TID:{} CheckWork thread {} returned".format(str(self.thread_id), str(i)))

            ## combine the failed chunks (if any) of all CheckWork() threads into jobHandles[]
            jobHandles = []
            debug_ctr = 0
            for i, array in enumerate(job_handles_temp):
                jobHandles += array
                self.logger.debug("TID:{} CheckWork thread {} length: {}".format(str(self.thread_id), 
                    str(i), len(array)))
                debug_ctr += 1

            '''
            if we have failed jobs, requeue them

            chunkPaths takes a tuple whos contents look like ('/ute/outbox', 'movie_folder/chunk_name')
            we have the first element from the outbox var, but the second one we'll dig out from the 
            job's argument string

            FYI, jobHandles is also an array of tuples
            '''
            if len(jobHandles) > 0:
                chunk_paths = []
                for jobId, job in jobHandles:
                    task.requeue_job(job.args, self.target_queue, jobId)
                    jobPath = re.search(r' -o .*?(/.*\.265)', str(job.args))
                    subPath = jobPath.group(1).split(outbox)[-1][1:]
                    chunk_paths.append((outbox, subPath))

                    self.logger.debug("TID:{} jobPath: {}".format(str(self.thread_id), 
                    str(jobPath.group(1))))
                    self.logger.debug("TID:{} Requeuing {}".format(str(self.thread_id), str(jobId)))

                self.logger.info("TID:{} Waiting for requeued jobs to complete".format(str(self.thread_id)))

                '''
                recalculate some things for our requeue chunks. we don't want to re-check all
                chunks just to verify a small subset.
                '''
                if len(jobHandles) < tCount:
                    tCount = 1

                self.logger.debug("TID:{} chunkPaths len {}".format(str(self.thread_id), 
                str(len(chunk_paths))))
                checkSize = int(len(chunk_paths) / tCount)

                '''
                to avoid getting caught in an endless loop, we set a limit for how many times a failed
                chunk can be requeued. sometimes there's something wrong with the source video, or a
                worker, etc.

                if this gets triggered, we'll try to be helpful by: 
                - leaving chunks in place for a later attempt
                - attempting to build video anyway (hey, it might just work)
                - create scripts? for the mkvmerge commands (video/audio) so the user can try manually
                - create a local log file of what went wrong in the output folder
                '''
                if failCount > 2:
                    msg = "TID:{} Video has failed {} times. ".format(str(self.thread_id), str(failCount-1))
                    msg += "Attempting to rebuild video in current state, but will leave chunk files "
                    msg += "intact until video can be verified."
                    self.logger.info(str(msg))

                    retainChunks = True
                    break

                failCount += 1
                continue
            else:
                break


        ## wrap things up. merge chunks, merge audio, handle source file, etc
        self.logger.info("TID:{} Building new Matroska file".format(str(self.thread_id)))
        no_audio_file_path = task.rebuild_video(full_outbound_path)

        self.logger.info("TID:{} Merging audio tracks into new MKV file".format(str(self.thread_id)))
        self.task_item.finalFileName = task.merge_audio(no_audio_file_path, full_outbound_path)

        if retainChunks == False:
            sleep(1)
            self.logger.info("TID:{} Clearing out '.265' chunk files".format(str(self.thread_id)))
            task.clean_out_folder(self.task_item.fullOutboundPath, self.task_item.finalFileName)
        else:
            self.logger.info("TID:{} Retaining '.265' chunk files".format(str(self.thread_id)))

        sleep(1)
        self.task_item.completed()
        self.logger.info("\nTID:{} Finished {}".format(str(self.thread_id), str(self.task_item)))



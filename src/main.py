import re, os, math, sys
import shutil
import subprocess
import logging.handlers
import threading

from typing import List
from logging import Logger, getLogger
from collections import deque
from pathlib import Path
from time import sleep
from redis import Redis
from rq import Queue, Worker

from ute.config import load
from ute.items import find_work
from ute.manager import TaskManager
from ute.task import Task


def setupLogging(config) -> Logger:
    ## logging setup for aggregator container
    root_logger = getLogger('ute')
    root_logger.setLevel(config["log_level"])
    socket_handler = logging.handlers.SocketHandler('aggregator', 
        logging.handlers.DEFAULT_TCP_LOGGING_PORT)

    root_logger.addHandler(socket_handler)
    logger = getLogger('ute.main')
    return logger


def purgeQueue(q, logger: Logger) -> None:
    ## delete all pending tasks in the Broker queue
    q.empty()
    logger.info("Queued jobs purged")


def purgeActive(q, logger: Logger) -> None:
    ## kill all active tasks
    for job in q.jobs:
        if job.get_status() == 'started':
            job.cancel

    logger.info("All tasks purged")


def getConfiguration():
    ## retrieve UTE global config settings
    return load("/ute.yaml")


def getClientCount(redis_link: Redis, worker_list: List[Worker], logger: Logger) -> List[Worker]:
    workers = Worker.all(connection=redis_link)
    logger.debug("Found " + str(len(workers)) + " workers")

    if not len(workers) == len(worker_list):
        change = set(workers).symmetric_difference(set(worker_list))
        if len(workers) > len(worker_list):
            logger.info("New worker(s): ")
            for worker in change:
                logger.info(str(worker.name))
        else:
            logger.info("Lost worker(s): ")
            for worker in change:
                logger.info(str(worker.name))

        logger.info("{} active workers".format(len(workers)))

    return workers



def main() -> None: 
    ## note: two types of queues. one is a redis queue, the other is a python
    ## FIFO queue for organizing files
    config = getConfiguration()
    logger = setupLogging()
    redis_link = Redis('redis')
    redis_queues = {
        'low'       : Queue('low', connection=redis_link),
        'high'      : Queue('low', connection=redis_link),
        'preprocess': Queue('low', connection=redis_link),
        'dummy'     : Queue('low', connection=redis_link)
    }
    work_queues = {
        'low'       : deque(),
        'high'      : deque(),
        'preprocess' : deque()
    }
    thread_keeper = {}
    thread_id, high_threads, low_threads = 0, 0, 0

    ## check for available workers
    worker_list = getClientCount(redis_link, [], logger)
    worker_count = len(worker_list)
    if worker_count == 0:
        logger.debug("No workers, sleeping")
        while len(getClientCount(redis_link, worker_list, logger)) == 0:
            sleep(20)

    while len(worker_list) > 0:
        ## check for files
        task_items = find_work(
            work_queues, thread_keeper, logger, config
        )
        if len(task_items) == 0 and len(thread_keeper) == 0:
            logger.info("No files; UTE sleeping")
            while len(task_items) == 0:
                task_items = find_work(
                    work_queues, thread_keeper, logger, config
                )
                sleep(20)

        ## determine if we're pulling work from High or Low queue
        target_queue = None
        task_item = None
        new_thread_flag = False
        high_priority_flag = False

        if high_threads < 2 and len(work_queues['high']) > 0:
            try:
                logger.debug("Queing high priority work")
                task_item = work_queues['high'].popleft()
                target_queue = redis_queues['high']
                new_thread_flag = True
                high_priority_flag = True

                '''
                if there's already a thread, delay creation of a second thread to
                avoid interleaving thread2 jobs with thread1 jobs, since it takes
                a few seconds to push jobs to Redis
                '''
                if high_threads == 1:
                    sleep(20)

                high_threads += 1
            except IndexError:
                logger.error("Unable to find files in High Queue")

        elif low_threads < 2 and len(work_queues['low']) > 0:
            try:
                logger.debug("Queing low priority work")
                task_item = work_queues['low'].popleft()
                target_queue = redis_queues['low']
                new_thread_flag = True

                '''
                if there's already a thread, delay creation of a second thread to
                avoid interleaving thread2 jobs with thread1 jobs, since it takes
                a few seconds to push jobs to Redis
                '''
                if low_threads == 1:
                    logger.debug("sleeping thread create")
                    sleep(20)

                low_threads += 1
            except IndexError:
                logger.error("Unable to find files in Low Queue")

        '''
        if it was determined above that a new thread should be created,
        make it and start it, adding the thread object to thread_keeper
        '''
        if new_thread_flag:
            ## create a numerical id to differentiate threads in log output
            thread_id += 1
            if thread_id > 9999:
                thread_id = 1

            tm = TaskManager(target_queue, redis_link, task_item, worker_count, thread_id, redis_queues['dummy'])
            thread = threading.Thread(target = tm.start())

            logger.debug("Starting thread")
            thread.start()
            if high_priority_flag:
                thread_keeper[(task_item, 'high')] = thread
            else:
                thread_keeper[(task_item, 'low')] = thread

        ## join finished threads 
        delete_thread_item = None
        if len(thread_keeper) > 0:
            for (name, priority), thread in thread_keeper.items():
                if not thread.is_alive():
                    logger.debug("Joining thread " + name)
                    thread.join()
                    delete_thread_item = (name, priority)
                    if priority == 'high':
                        high_threads -= 1
                    else:
                        low_threads -= 1

        if not delete_thread_item == None:
            del thread_keeper[delete_thread_item]

        sleep(5)



if __name__ == "__main__":
    main()





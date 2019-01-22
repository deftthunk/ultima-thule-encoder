from .tasks import *
import time


# ping all Celery nodes and count number of responses
def getClientCount():
    ping = app.control.ping(timeout=1.0)
    return len(ping)


if __name__ == '__main__':
    ping = app.control.ping(timeout=0.5)
    print("hosts: ", len(ping))


'''
if __name__ == '__main__':
    result = longtime_add.delay(1,2)
    result2 = longtime_add.delay(4,2)
    result5 = subtract.delay(5,2)
    # at this time, our task is not finished so it will return False
    print('Task finished? ', result.ready())
    print('Task result: ', result.result)

    # sleep 10 sec to ensure task is finished
    time.sleep(10)

    # now the task should be finished and ready to return True
    print('Task finished? ', result.ready())
    print('Task result: ', result.result)
'''



from time import sleep
import re, os
import logging
import subprocess

#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.basicConfig(level=logging.DEBUG)

## take a CLI command from the task and execute it via subprocess module
## the decorator '@app.task' enables retrying for failed tasks which
## will be seen in Flower
def encode(cmd):
    #logging.debug("CMD:: >> " + str(cmd))
    logging.debug("")
    status = subprocess.run(cmd, shell=True, stderr=subprocess.STDOUT, \
        stdout=subprocess.PIPE)

    #logging.debug("CMD STDOUT:: >> " + str(status.stdout))
    logging.debug("")
    logging.info("Status: " + str(status.returncode))
    fps = parseFPS(status.stdout)
    fileCheck(cmd)
    logging.debug("====================================")
    logging.debug("")
    logging.debug("")
    logging.debug("")

    return fps


## find the last printout of x265's 'frames per second' estimate, which ought
## to be the average processing speed of the node its running on
def parseFPS(string):
    fps = re.findall(r'\s(\d+\.\d+)\sfps', string.decode('utf-8'))

    ## debugging stuff
    #logging.debug("fps output:: >> " + str(string.decode('utf-8')))
    logging.debug("fps size:: >>" + str(len(fps)))
    
    if len(fps) > 0:
        return fps[-1]
    else:
        logging.debug("fps return fail!!!")
        return 00
    

## debugging function
def fileCheck(cmd):
    filePath = re.findall(r'\s-o\s(/ute/outbox.*)$', cmd)
    if len(filePath) > 0:
        statInfo = os.stat(filePath[-1])

        if statInfo.st_size == 0:
            logging.debug("<><><><><><><><><><><>")
            logging.debug("FAILED: 0 Byte file")
            logging.debug("<><><><><><><><><><><>")
            memCheck()


## container memory usage
def memCheck():
    ret = subprocess.run('cat /proc/meminfo', shell=True, stderr=subprocess.STDOUT, \
            stdout=subprocess.PIPE)
    memInfo = ret.stdout
    memTotal = re.search(r'MemTotal:\s+(\d+)\skB', memInfo.decode('utf-8'))
    memFree = re.search(r'MemFree:\s+(\d+)\skB', memInfo.decode('utf-8'))

    logging.debug("MemTotal: " + str(int(memTotal.group(1)) / 1024))
    logging.debug("MemFree: " + str(int(memFree.group(1)) / 1024))








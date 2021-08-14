import files
import logging
import config
from collections import deque

threadKeeper = {}
internal = {'low':deque(), 'high':deque(), 'preprocess':deque()}
logger = logging.getLogger('test')
conf = config.load("global.uteconf.yaml")

 
ret = files.FindWork(internal, threadKeeper, logger, conf)
for f in ret:
    print(f.video_file_path)

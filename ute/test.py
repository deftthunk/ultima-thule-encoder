from logging import getLogger
from collections import deque
from ute.config import load
from ute.files import findWork

threadKeeper = {}
internal = {'low':deque(), 'high':deque(), 'preprocess':deque()}
logger = getLogger('test')
conf = load("global.uteconf.yaml")

 
ret = findWork(internal, threadKeeper, logger, conf)
for f in ret:
    print(f.video_file_path)

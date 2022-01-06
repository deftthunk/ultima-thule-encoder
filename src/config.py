from logging import getLogger
from ruamel.yaml import yaml, Loader
from copy import deepcopy


def load(file_path: str):
    fh = open(file_path, 'r')
    return yaml.load(fh, Loader=Loader)


def override(current_config_obj, new_config_path):
    fh_new = open(new_config_path)
    new_config = yaml.load(fh_new, Loader=Loader)
    ## do a deep copy since we don't want to affect settings for other tasks
    old_config = deepcopy(current_config_obj)

    new_task_conf = new_config.get("task")
    if new_task_conf != None:
        old_task_conf = old_config.get("task")
        for key in new_task_conf.keys():
            old_task_conf[key] = new_task_conf[key]

        return old_config
    else:
        log = getLogger("ute.config")
        msg = "Unable to override config settings using {} file. ".format(str(new_config_path))
        msg += "Cannot find 'task' key in new settings. Proceeding with current settings"
        log.warning(str(msg))

        return current_config_obj
    
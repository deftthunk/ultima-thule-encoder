#from dataclasses import dataclass
from pathlib import Path
from ruamel import yaml

#@dataclass
class Config:
  


  fh_in = open('/wd/ute.yaml', 'r')
  config = yaml.load(fh_in)


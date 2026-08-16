#!/usr/bin/env python3
try:
    from .pidog import Pidog
    from robot_hat import utils
except ImportError:
    Pidog = None
    utils = None
from time import sleep
from .version import __version__

def __main__():
    print(f"Thanks for using Pidog {__version__} ! woof, woof, woof !")
    if utils:
        utils.reset_mcu()
    sleep(0.2)


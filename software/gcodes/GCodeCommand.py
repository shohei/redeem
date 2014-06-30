'''
Abstract definition for GCode command processor classes

Author: Mathieu Monney
email: zittix(at)xwaves(dot)net
Website: http://www.xwaves.net
License: CC BY-SA: http://creativecommons.org/licenses/by-sa/2.0/
'''

from abc import ABCMeta, abstractmethod
import logging

class GCodeCommand(object):
    __metaclass__ = ABCMeta
    
    def __init__(self, printer):
        self.printer=printer


    @abstractmethod
    def execute(self,gcode):
        pass

    @abstractmethod
    def get_description(self):
        pass
    
    ''' Return true if the command has to wait in the command buffer or false to be executed immediately '''
    def is_buffered(self):
        return True

    ''' The class name of the gcode '''
    def __str__(self):
        return type(self).__name__

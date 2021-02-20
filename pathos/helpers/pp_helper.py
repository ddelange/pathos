#!/usr/bin/env python
#
# Author: Mike McKerns (mmckerns @caltech and @uqfoundation)
# Copyright (c) 1997-2016 California Institute of Technology.
# Copyright (c) 2016-2021 The Uncertainty Quantification Foundation.
# License: 3-clause BSD.  The full license text is available at:
#  - https://github.com/uqfoundation/pathos/blob/master/LICENSE

from __future__ import print_function
try:
    from multiprocess import TimeoutError
    from multiprocess.pool import MapResult as _MapResult
    from multiprocess.pool import ApplyResult as _ApplyResult
    HAS_FORK = True
except ImportError:
    HAS_FORK = False

try:
    if HAS_FORK: raise ValueError
    from processing import TimeoutError
    from processing.pool import MapResult as _MapResult
    from processing.pool import ApplyResult as _ApplyResult
except ImportError:  # fall-back to package distributed with python
    from multiprocessing import TimeoutError
    from multiprocessing.pool import MapResult as _MapResult
    from multiprocessing.pool import ApplyResult as _ApplyResult
except ValueError: pass
del HAS_FORK

from pp import _Task
from pp import Server
import time
import dill as pickle
import threading

#FIXME: this is a sloppy kludge... inheritance or not, this is not clean
class ApplyResult(_Task): #XXX: better if not derived from _Task?
    """result object for an 'apply' method in parallelpython

enables a pp._Task to mimic the multiprocessing.pool.ApplyResult interface
    """
    #XXX: allow callback etc in __init__ ?
    def __init__(self, task):# callback=None, callbackargs=(), group='default'):
        if not isinstance(task, _Task):
            msg = "a pp._Task (generated by server.submit) is required"
            raise TypeError(msg)
        #interface: _Task
        self.unpickled = False
        #interface: _ApplyResult
        self._task = task
        self._success = True
        return

    def ready(self):
        "Checks if the result is ready"
        return self.finished

    def successful(self):
        "Measures whether result is ready and loaded w/o printing"
        assert self.ready()
        if not self.unpickled: self.__unpickle()
        return self._success

    def __unpickle(self):
        """Unpickles the result of the task"""
        self.result, sout = pickle.loads(self._task.sresult)
        self.unpickled = True
        if len(sout) > 0:
            print(sout, end=' ')   #XXX: breaks 2.5 compatibility
            self._success = False  #XXX: we assume sout>0 is an error
        else: self._success = True #XXX: we assume sout=0 is ok
        if self.callback:
            args = self.callbackargs + (self.result, )
            self.callback(*args)

    def wait(self, timeout=None): #XXX: None is blocking
        """Waits for the task""" 
        if not self.finished:
            cond = threading.Condition(self._task.lock) #XXX: or need Rlock???
            cond.acquire()
            try:
                if not self._task.finished:
                    cond.wait(timeout) #FIXME: ignores timeout, and blocks
            finally:
                cond.release()
        return

    def get(self, timeout=None):
        "Retrieves result of the task"
        self.wait(timeout)
        if not self.finished: raise TimeoutError
        return self.__call__()

    def __call__(self, raw_result=False):
        """Retrieves result of the task"""
        self.wait()
        if not self.unpickled and not raw_result:
            self.__unpickle()
        if raw_result:
            return self._task.sresult
        else:
            return self.result

    def finalize(self, sresult):
        """Finalizes the task  ***internal use only***"""
        self._task.sresult = sresult
        if self.callback:
            self.__unpickle()
        self.finished = True

    #interface: _Task
    @property
    def lock(self):
        return self._task.lock
    @property
    def tid(self):
        return self._task.tid
    @property
    def server(self):
        return self._task.server
    @property
    def callback(self):
        return self._task.callback
    @property
    def callbackargs(self):
        return self._task.callbackargs
    @property
    def group(self):
        return self._task.group
    @property
    def finished(self):
        return self._task.finished
    pass


class MapResult(object):

    def __init__(self, size, callback=None, callbackargs=(), group='default'):
        chunksize, length = size
        #interface: ApplyResult
        self.callback = callback
        self.callbackargs = callbackargs
        self.group = group
        self.finished = False
        self.unpickled = False
        self._success = True
        #interface: _MapResult
        self._value = [None] * length
        self._chunksize = chunksize
        if chunksize <= 0:
            self._number_left = 0
            self.finished = True
        else:
            self._number_left = length//chunksize + bool(length % chunksize)
        #interface: list
        self.__queue = ()
        self.__tasks = []
        return

    def finalize(self, *results): # should be a 'sresult' (pickled result)
        "finalize the tasks  ***internal use only***"
        [task.finalize(result) for (task,result) in zip(self.__tasks,results)]
        if self.callback:
            self.__unpickle() #XXX: better known as 'fetch the results'
        self.finished = True
        return #FIXME: this is probably broken... needs testing!!!

    def __unpickle(self):
        """Unpickles the results of the tasks"""
        if not self.unpickled:
            self.__queue = list(self.__queue) #XXX: trigger fetch of results
            self.unpickled = True
        if self.callback:
            args = self.callbackargs + (self._value, )
            self.callback(*args)

    def queue(self, *tasks): # expects list of ApplyResult objects
        "Fill the MapResult with ApplyResult objects"
        valid = [isinstance(task, ApplyResult) for task in tasks]
        if not all(valid):
            tasks = list(tasks)
            _valid = [isinstance(task, _Task) for task in tasks]
            if not all(_valid): #XXX: above could be more efficient
                id = _valid.index(False)
                msg = "%s is not a pp._Task instance" % tasks[id]
                raise TypeError(msg)
            while valid.count(False):
                ind = valid.index(False)
                tasks[ind] = ApplyResult(tasks[ind])
                valid[ind] = True
        self.__queue = (self._set(i,task) for (i,task) in enumerate(tasks))
        self.__tasks = tasks
        self.finished = False
        self.unpickled = False
        return

    def __call__(self):
        """Retrieve the results of the tasks"""
        self.wait()
        if not self.unpickled:
            self.__unpickle()
        return self._value

    def wait(self, timeout=None):
        "Wait for the tasks"
        if not self.ready():
            for task in self.__tasks:
                task.wait(timeout)
                #XXX: better one-time timeout or n-time ?
                if timeout is None:
                    continue
                timeout = 0
       #return self.ready() #XXX: better if return T/F ?
           #if self.ready():
           #    self.__unpickle() #XXX: better if callback...?
       #return

    def get(self, timeout=None):
        "Retrieves results of the tasks"
        self.wait(timeout)
        if not self.ready(): raise TimeoutError
        return self.__call__()

    def ready(self):
        "Checks if the result is ready"
        self.finished = all([task.finished for task in self.__tasks])
        return self.finished

    def successful(self):
        "Measures whether result is ready and loaded w/o printing"
        assert self.ready()
        if not self.unpickled: self.__unpickle()
        return self._success

    def _set(self, i, task): #XXX: unordered by how fill _value & imap in _set?
        task.wait()
        success, result = task.successful(), [task.result]
        if success:
            self._value[i*self._chunksize:(i+1)*self._chunksize] = result
            self._number_left -= 1
            if self._number_left == 0:
                self._success = True
                self.unpickled = True
               #self.__unpickle()
                self.finished = True
        else:
            self._success = False
            self.unpickled = True
            self._value = result
            print(result, end=' ') #XXX: breaks 2.5 compatibility
            self.finished = True
        return task

    pass



# EOF

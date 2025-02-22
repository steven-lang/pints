#
# Utility classes to perform multiple model evaluations sequentially or in
# parallell.
#
# This file is part of PINTS (https://github.com/pints-team/pints/) which is
# released under the BSD 3-clause license. See accompanying LICENSE.md for
# copyright notice and full license details.
#
# Some code in this file was adapted from Myokit (see http://myokit.org)
#
from __future__ import absolute_import, division
from __future__ import print_function, unicode_literals
import gc
import os
import sys
import time
import traceback
import multiprocessing
try:
    # Python 3
    import queue
except ImportError:
    import Queue as queue


def evaluate(f, x, parallel=False, args=None):
    """
    Evaluates the function ``f`` on every value present in ``x`` and returns
    a sequence of evaluations ``f(x[i])``.

    Parameters
    ----------
    f : callable
        The function to evaluate, called as ``f(x[i], *args)``.
    x
        A list of values to evaluate ``f`` with
    parallel : boolean
        Run in parallel or not.
        If set to ``True``, the evaluations will happen in parallel using a
        number of worker processes equal to the detected cpu core count. The
        number of workers can be set explicitly by setting ``parallel`` to an
        integer greater than 0.
        Parallelisation can be disabled by setting ``parallel`` to ``0`` or
        ``False``.
    args : sequence
        Optional extra arguments to pass into ``f``.


    """
    if parallel is True:
        evaluator = ParallelEvaluator(f, args=args)
    elif parallel >= 1:
        evaluator = ParallelEvaluator(f, n_workers=int(parallel), args=args)
    else:
        evaluator = SequentialEvaluator(f, args=args)
    return evaluator.evaluate(x)


class Evaluator(object):
    """
    Abstract base class for classes that take a function (or callable object)
    ``f(x)`` and evaluate it for list of input values ``x``. This interface is
    shared by a parallel and a sequential implementation, allowing easy
    switching between parallel or sequential implementations of the same
    algorithm.

    Parameters
    ----------
    function : callable
        A function or other callable object ``f`` that takes a value ``x`` and
        returns an evaluation ``f(x)``.
    args : sequence
        An optional sequence of extra arguments to ``f``. If ``args`` is
        specified, ``f`` will be called as ``f(x, *args)``.
    """
    def __init__(self, function, args=None):

        # Check function
        if not callable(function):
            raise ValueError('The given function must be callable.')
        self._function = function

        # Check args
        if args is None:
            self._args = ()
        else:
            try:
                len(args)
            except TypeError:
                raise ValueError(
                    'The argument `args` must be either None or a sequence.')
            self._args = args

    def evaluate(self, positions):
        """
        Evaluate the function for every value in the sequence ``positions``.

        Returns a list with the returned evaluations.
        """
        try:
            len(positions)
        except TypeError:
            raise ValueError(
                'The argument `positions` must be a sequence of input values'
                ' to the evaluator\'s function.')
        return self._evaluate(positions)

    def _evaluate(self, positions):
        """ See :meth:`evaluate()`. """
        raise NotImplementedError


class ParallelEvaluator(Evaluator):
    """
    Evaluates a single-valued function object for any set of input values
    given, using all available cores.

    Shares an interface with the :class:`SequentialEvaluator`, allowing
    parallelism to be switched on and off with minimal hassle. Parallelism
    takes a little time to be set up, so as a general rule of thumb it's only
    useful for if the total run-time is at least ten seconds (anno 2015).

    By default, the number of processes ("workers") used to evaluate the
    function is set equal to the number of CPU cores reported by python's
    ``multiprocessing`` module. To override the number of workers used, set
    ``n_workers`` to some integer greater than ``0``.

    There are two important caveats for using multiprocessing to evaluate
    functions:

      1. Processes don't share memory. This means the function to be
         evaluated will be duplicated (via pickling) for each process (see
         `Avoid shared state <http://docs.python.org/2/library/\
multiprocessing.html#all-platforms>`_ for details).
      2. On windows systems your code should be within an
         ``if __name__ == '__main__':`` block (see `Windows
         <https://docs.python.org/2/library/multiprocessing.html#windows>`_
         for details).

    The evaluator will keep it's subprocesses alive and running until it is
    tidied up by garbage collection.

    Note that while this class uses multiprocessing, it is not thread/process
    safe itself: It should not be used by more than a single thread/process at
    a time.

    Extends :class:`Evaluator`.

    Parameters
    ----------
    function
        The function to evaluate
    n_workers
        The number of worker processes to use. If left at the default value
        ``n_workers=None`` the number of workers will equal the number of CPU
        cores in the machine this is run on. In many cases this will provide
        good performance.
    max_tasks_per_worker
        Python garbage collection does not seem to be optimized for
        multi-process function evaluation. In many cases, some time can be
        saved by refreshing the worker processes after every
        ``max_tasks_per_worker`` evaluations. This number can be tweaked for
        best performance on a given task / system.
    args
        An optional sequence of extra arguments to ``f``. If ``args`` is
        specified, ``f`` will be called as ``f(x, *args)``.
    """
    def __init__(
            self, function,
            n_workers=None,
            max_tasks_per_worker=500,
            args=None):
        super(ParallelEvaluator, self).__init__(function, args)

        # Determine number of workers
        if n_workers is None:
            self._n_workers = ParallelEvaluator.cpu_count()
        else:
            self._n_workers = int(n_workers)
            if self._n_workers < 1:
                raise ValueError(
                    'Number of workers must be an integer greater than 0 or'
                    ' `None` to use the default value.')

        # Create empty set of workers
        self._workers = []

        # Maximum tasks per worker (for some reason, this saves memory)
        self._max_tasks = int(max_tasks_per_worker)
        if self._max_tasks < 1:
            raise ValueError(
                'Maximum tasks per worker should be at least 1 (but probably'
                ' much greater).')

        # Queue with tasks
        self._tasks = multiprocessing.Queue()

        # Queue with results
        self._results = multiprocessing.Queue()

        # Queue used to add an exception object and context to
        self._errors = multiprocessing.Queue()

        # Flag set if an error is encountered
        self._error = multiprocessing.Event()

    def __del__(self):
        # Cancel everything
        try:
            self._stop()
        except Exception:
            pass

    def _clean(self):
        """
        Cleans up any dead workers & return the number of workers tidied up.
        """
        cleaned = 0
        for k in range(len(self._workers) - 1, -1, -1):
            w = self._workers[k]
            if w.exitcode is not None:  # pragma: no cover
                w.join()
                cleaned += 1
                del(self._workers[k], w)
        if cleaned:     # pragma: no cover
            gc.collect()
        return cleaned

    @staticmethod
    def cpu_count():
        """
        Uses the multiprocessing module to guess the number of available cores.

        For machines with simultaneous multithreading ("hyperthreading") this
        will return the number of virtual cores.
        """
        return max(1, multiprocessing.cpu_count())

    def _populate(self):
        """
        Populates (but usually repopulates) the worker pool.
        """
        for k in range(self._n_workers - len(self._workers)):
            w = _Worker(
                self._function,
                self._args,
                self._tasks,
                self._results,
                self._max_tasks,
                self._errors,
                self._error,
            )
            self._workers.append(w)
            w.start()

    def _evaluate(self, positions):
        """
        Evaluate all tasks in parallel, in batches of size self._max_tasks.
        """
        # Ensure task and result queues are empty
        # For some reason these lines block when running on windows
        # if not (self._tasks.empty() and self._results.empty()):
        #    raise Exception('Unhandled tasks/results left in queues.')
        # Clean up any dead workers
        self._clean()

        # Ensure worker pool is populated
        self._populate()

        # Start
        try:

            # Enqueue all tasks (non-blocking)
            for k, x in enumerate(positions):
                self._tasks.put((k, x))

            # Collect results (blocking)
            n = len(positions)
            m = 0
            results = [0] * n
            while m < n and not self._error.is_set():
                time.sleep(0.001)   # This is really necessary
                # Retrieve all results
                try:
                    while True:
                        i, f = self._results.get(block=False)
                        results[i] = f
                        m += 1
                except queue.Empty:
                    pass

                # Clean dead workers
                if self._clean():  # pragma: no cover
                    # Repolate
                    self._populate()

        except (IOError, EOFError):     # pragma: no cover
            # IOErrors can originate from the queues as a result of issues in
            # the subprocesses. Check if the error flag is set. If it is, let
            # the subprocess exception handling deal with it. If it isn't,
            # handle it here.
            if not self._error.is_set():
                self._stop()
                raise
            # TODO: Maybe this should be something like while(error is not set)
            # wait for it to be set, then let the subprocess handle it...

        except (Exception, SystemExit, KeyboardInterrupt):  # pragma: no cover
            # All other exceptions, including Ctrl-C and user triggered exits
            # should (1) cause all child processes to stop and (2) bubble up to
            # the caller.
            self._stop()
            raise

        # Error in worker threads
        if self._error.is_set():
            errors = self._stop()
            # Raise exception
            if errors:
                pid, trace = errors[0]
                raise Exception(
                    'Exception in subprocess:\n' + trace
                    + '\nException in subprocess')
            else:
                # Don't think this is reachable!
                raise Exception(
                    'Unknown exception in subprocess.')  # pragma: no cover

        # Return results
        return results

    def _stop(self):
        """
        Forcibly halts the workers
        """
        time.sleep(0.1)

        # Terminate workers
        for w in self._workers:
            if w.exitcode is None:
                w.terminate()
        for w in self._workers:
            if w.is_alive():
                w.join()
        self._workers = []

        # Clear queues
        def clear(q):
            items = []
            try:
                while True:
                    items.append(q.get(timeout=0.1))
            except (queue.Empty, IOError, EOFError):
                pass
            return items

        clear(self._tasks)
        clear(self._results)
        errors = clear(self._errors)

        # Create new queues & error event
        self._tasks = multiprocessing.Queue()
        self._results = multiprocessing.Queue()
        self._errors = multiprocessing.Queue()
        self._error = multiprocessing.Event()

        # Free memory
        gc.collect()

        # Return errors
        return errors


class SequentialEvaluator(Evaluator):
    """
    Evaluates a function (or callable object) for a list of input values, and
    returns a list containing the calculated function evaluations.

    Runs sequentially, but shares an interface with the
    :class:`ParallelEvaluator`, allowing parallelism to be switched on/off.

    Extends :class:`Evaluator`.

    Parameters
    ----------
    function : callable
        The function to evaluate.
    args : sequence
        An optional tuple containing extra arguments to ``f``. If ``args`` is
        specified, ``f`` will be called as ``f(x, *args)``.
    """
    def __init__(self, function, args=None):
        super(SequentialEvaluator, self).__init__(function, args)

    def _evaluate(self, positions):
        scores = [0] * len(positions)
        for k, x in enumerate(positions):
            scores[k] = self._function(x, *self._args)
        return scores


#
# Note: For Windows multiprocessing to work, the _Worker can never be a nested
# class!
#
class _Worker(multiprocessing.Process):
    """
    Worker class for use with :class:`ParallelEvaluator`.

    Evaluates a single-valued function for every point in a ``tasks`` queue
    and places the results on a ``results`` queue.

    Keeps running until it's given the string "stop" as a task.

    Extends ``multiprocessing.Process``.

    Parameters
    ----------
    function : callable
        The function to optimize.
    args : sequence
        A (possibly empty) tuple containing extra input arguments to the
        objective function.
    tasks
        The queue to read tasks from. Tasks are stored as tuples
        ``(i, p)`` where ``i`` is a task id and ``p`` is the
        position to evaluate.
    results
        The queue to store results in. Results are stored as
        tuples ``(i, p, r)`` where ``i`` is the task id, ``p`` is
        the position evaluated (which can be updated by the
        refinement method!) and ``r`` is the result at ``p``.
    max_tasks : int
        The maximum number of tasks to perform before dying.
    errors
        A queue to store exceptions on
    error
        This flag will be set by the worker whenever it encounters an
        error.
    """
    def __init__(
            self, function, args, tasks, results, max_tasks, errors, error):
        super(_Worker, self).__init__()
        self.daemon = True
        self._function = function
        self._args = args
        self._tasks = tasks
        self._results = results
        self._max_tasks = max_tasks
        self._errors = errors
        self._error = error

    def run(self):
        # Worker processes should never write to stdout or stderr.
        # This can lead to unsafe situations if they have been redirected to
        # a GUI task such as writing to the IDE console.
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        try:
            for k in range(self._max_tasks):
                i, x = self._tasks.get()
                f = self._function(x, *self._args)
                self._results.put((i, f))

                # Check for errors in other workers
                if self._error.is_set():
                    return

        except (Exception, KeyboardInterrupt, SystemExit):
            self._errors.put((self.pid, traceback.format_exc()))
            self._error.set()


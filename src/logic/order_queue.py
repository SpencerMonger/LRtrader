from functools import wraps
import queue
from queue import Queue
from threading import Thread
from typing import Callable

from loguru import logger


class OrderQueue:
    def __init__(self):
        self.queue = Queue()
        self.worker_thread = None
        self.is_running = False

    def start(self):
        """Start the worker thread."""
        self.is_running = True
        self.worker_thread = Thread(target=self._worker)
        self.worker_thread.start()

    def stop(self):
        """Stop the worker thread."""
        self.is_running = False
        # Add a sentinel value to wake up the worker thread immediately
        self.queue.put((None, None, None))
        if self.worker_thread:
            self.worker_thread.join(timeout=5)  # Add timeout to prevent infinite wait
            if self.worker_thread.is_alive():
                logger.warning("OrderQueue worker thread did not stop within timeout")

    def enqueue(self, func: Callable, *args, **kwargs):
        """Enqueue a function call."""
        if self.is_running:  # Only enqueue if running
            self.queue.put((func, args, kwargs))

    def _worker(self):
        """Worker thread to process queued function calls."""
        while self.is_running:
            try:
                func, args, kwargs = self.queue.get(timeout=1)
                
                # Check for sentinel value (stop signal)
                if func is None:
                    break
                    
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    logger.error(f"Error executing queued function {func.__name__}: {e}")
                finally:
                    self.queue.task_done()
            except queue.Empty:
                continue


def queued_execution(func):
    """
    Decorator to enqueue a method call instead of executing it immediately.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.order_queue.enqueue(func, self, *args, **kwargs)

    return wrapper

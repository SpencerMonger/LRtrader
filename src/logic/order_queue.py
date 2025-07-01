from functools import wraps
import queue
import time
from queue import Queue
from threading import Thread
from typing import Callable, Dict

from loguru import logger


class OrderQueue:
    def __init__(self, staggered_order_delay: float = 5.0):
        self.queue = Queue()
        self.worker_thread = None
        self.is_running = False
        # Track last entry order time per ticker for staggered delays
        self.last_entry_order_time: Dict[str, float] = {}
        self.entry_order_delay = staggered_order_delay  # Configurable delay between entry orders

    def start(self):
        """Start the worker thread."""
        self.is_running = True
        self.worker_thread = Thread(target=self._worker)
        self.worker_thread.start()

    def stop(self):
        """Stop the worker thread."""
        self.is_running = False
        # Add a sentinel value to wake up the worker thread immediately
        self.queue.put((None, None, None, None))
        if self.worker_thread:
            self.worker_thread.join(timeout=5)  # Add timeout to prevent infinite wait
            if self.worker_thread.is_alive():
                logger.warning("OrderQueue worker thread did not stop within timeout")

    def enqueue(self, func: Callable, *args, **kwargs):
        """Enqueue a function call."""
        if self.is_running:  # Only enqueue if running
            # Extract ticker from args if this is a handle_prediction call
            ticker = None
            if hasattr(args[0], 'assignment') and hasattr(args[0].assignment, 'ticker'):
                ticker = args[0].assignment.ticker
            
            self.queue.put((func, args, kwargs, ticker))

    def _worker(self):
        """Worker thread to process queued function calls."""
        while self.is_running:
            try:
                func, args, kwargs, ticker = self.queue.get(timeout=1)
                
                # Check for sentinel value (stop signal)
                if func is None:
                    break
                
                # Apply staggered delay for entry orders
                if ticker and func.__name__ == 'handle_prediction':
                    current_time = time.time()
                    last_time = self.last_entry_order_time.get(ticker, 0)
                    time_since_last = current_time - last_time
                    
                    if time_since_last < self.entry_order_delay:
                        delay_needed = self.entry_order_delay - time_since_last
                        logger.info(f"[{ticker}] Applying staggered delay: {delay_needed:.1f}s")
                        time.sleep(delay_needed)
                    
                    # Update last entry order time
                    self.last_entry_order_time[ticker] = time.time()
                    
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

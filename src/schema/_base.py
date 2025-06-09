"""
The schema model requires a thread safe lock to ensure that we manage our positions safly.
"""

import threading

from pydantic import BaseModel, PrivateAttr


class ThreadSafeModel(BaseModel):
    """
    Base class for thread-safe models.

    This class provides a thread-safe lock to ensure that the model is
    accessed safely in a multi-threaded environment.

    :param threading.Lock _lock: The lock to ensure thread safety.
    """

    _lock: threading.Lock = PrivateAttr(...)

    def __init__(self, **data):
        super().__init__(**data)
        self._lock = threading.Lock()

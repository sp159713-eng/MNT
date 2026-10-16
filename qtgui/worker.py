from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Task(QRunnable):
    def __init__(self, owner, token, function):
        super().__init__()
        self.owner = owner
        self.token = token
        self.function = function

    def run(self):
        try:
            result, error = self.function(), None
        except Exception as failure:
            result, error = None, failure
        try:
            self.owner.finished.emit(self.token, result, error)
        except RuntimeError:
            pass


class Worker(QObject):
    finished = Signal(int, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.callbacks = {}
        self.counter = 0
        self.finished.connect(self._deliver)

    def submit(self, function, on_done, on_failed):
        self.counter += 1
        token = self.counter
        self.callbacks[token] = (on_done, on_failed)
        self.pool.start(_Task(self, token, function))

    def _deliver(self, token, result, error):
        on_done, on_failed = self.callbacks.pop(token)
        try:
            if error is not None:
                on_failed(error)
            else:
                on_done(result)
        except RuntimeError as stale:
            if "already deleted" not in str(stale):
                raise

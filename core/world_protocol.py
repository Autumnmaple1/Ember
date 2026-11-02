"""A process/restore epoch and bounded event tail; no arbitrary database replay."""
import copy
import threading
import uuid
from functools import wraps
from contextlib import nullcontext
from collections import deque


class WorldProtocol:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = deque(maxlen=512)
        self.rotate()

    def rotate(self):
        with self.lock:
            self.epoch = str(uuid.uuid4())
            self.seq = 0
            self.state_version = 0
            self.events.clear()
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {'epoch': self.epoch, 'event_seq': self.seq, 'state_version': self.state_version}

    def envelope(self, event, epoch=None):
        with self.lock:
            if epoch and epoch != self.epoch:
                return None
            if event.get('epoch'):
                return event if event['epoch'] == self.epoch else None
            self.seq += 1
            if event.get('type') == 'state_update':
                self.state_version += 1
            result = {**event, **self.snapshot()}
            # Audio is transient and must never be replayed on reconnect.
            if result.get('type') not in ('audio', 'voice', 'tts_error', 'ping', 'world.snapshot', 'world.reset'):
                self.events.append(copy.deepcopy(result))
            return result

    def tail(self, epoch, after):
        with self.lock:
            complete = epoch == self.epoch and (not self.events or after >= self.events[0]['event_seq'] - 1)
            return {'complete': complete, 'events': [copy.deepcopy(x) for x in self.events
                if x['event_seq'] > after] if complete else [], **self.snapshot()}


def protocol_serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.protocol.lock if hasattr(self, 'protocol') else nullcontext():
            return method(self, *args, **kwargs)
    return wrapped

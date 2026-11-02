"""One lock for append and acknowledged-prefix consumption of experience logs."""
import threading
import json
from pathlib import Path

LOG_LOCK = threading.RLock()


def append_log(filename, content):
    with LOG_LOCK:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as file:
            file.write(content + '\n')


def consume_prefix(text, filename='config/chat_history.log'):
    with LOG_LOCK:
        path = Path(filename)
        current = path.read_text(encoding='utf-8') if path.exists() else ''
        if not current.startswith(text):
            return False
        temporary = path.with_suffix('.consume.tmp')
        temporary.write_text(current[len(text):], encoding='utf-8')
        temporary.replace(path)
        return True


def append_event_once(row, filename='config/chat_history.log'):
    with LOG_LOCK:
        path = Path(filename)
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    previous = json.loads(line)
                except ValueError:
                    continue
                if isinstance(previous, dict) and previous.get('message_id') == row['message_id']:
                    return
        append_log(filename, json.dumps(row, ensure_ascii=False))

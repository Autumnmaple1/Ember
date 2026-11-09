"""A recoverable backup worker and cached service status, one world writer."""
import logging
import os
import threading
import time
import uuid
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class ServiceRuntime:
    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.owner = str(uuid.uuid4())
        self.stop_event = threading.Event()
        self.thread = None
        self.interval = max(60, int(os.getenv('EMBER_BACKUP_INTERVAL', '86400')))
        self.cached = store.snapshot()
        self.error = None

    def schedule(self):
        slot = int(time.time() // self.interval)
        # The slot is a wall-clock schedule, unaffected by game time/restore.
        self.store.enqueue('maintenance.backup', f'backup:{slot}', {'slot': slot}, delay=10)

    def start(self, server):
        self.server = server
        self.schedule()
        self.thread = threading.Thread(target=self.run, daemon=True, name='ember-service-jobs')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def backup(self, job):
        server = self.server
        name = 'auto_backup_service_' + job['id']
        # A file committed before a process crash is an idempotent receipt.
        if server.archive_manager._archive_path(name).exists():
            if server.archive_manager.get_archive_preview(name) is not None:
                return {'slot_name': name, 'control_backup': self.backup_control(job), 'recovered_receipt': True}
            raise RuntimeError('已有备份未通过校验，保留文件等待检查')
        if server.archive_jobs.busy or server.brain._is_processing or server.event_bus.pending_dialogues:
            raise RuntimeError('正在处理对话或存档，稍后重试自动备份')
        task = server.archive_jobs.start('create', name, '常驻服务自动备份')
        while True:
            snapshot = server.archive_jobs.snapshot()
            if snapshot and snapshot['task_id'] == task['task_id'] and snapshot['status'] != 'running':
                if snapshot['status'] != 'completed':
                    raise RuntimeError('自动备份失败，已保留原状态')
                return {'slot_name': name, 'control_backup': self.backup_control(job)}
            if self.stop_event.wait(.2):
                raise RuntimeError('服务正在停止，备份回执将在下次启动核对')

    def backup_control(self, job):
        from config.settings import settings
        key = str(uuid.UUID(job['id']))
        folder = Path('service/backups').resolve()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'control_{key}.sql'
        if target.exists() and target.stat().st_size:
            return target.name
        temporary = target.with_suffix('.sql.tmp')
        result = subprocess.run(['pg_dump', '--host', settings.PG_HOST, '--port', str(settings.PG_PORT),
            '--username', settings.PG_USER, '--dbname', settings.PG_DB, '--schema', self.store.s,
            '--no-owner', '--no-privileges', '--file', str(temporary)],
            env={**os.environ, 'PGPASSWORD': settings.PG_PASSWORD}, capture_output=True, timeout=120)
        if result.returncode or not temporary.exists() or not temporary.stat().st_size:
            logger.error('服务身份与任务备份失败：%s', result.stderr[:500])
            raise RuntimeError('服务身份与任务备份未完成，已有世界备份已保留')
        temporary.chmod(0o600)
        with temporary.open('rb') as file:
            os.fsync(file.fileno())
        temporary.replace(target)
        # Only generated control backups in this exact directory are retained.
        candidates = sorted(folder.glob('control_*.sql'), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in candidates[3:]:
            if not old.is_symlink() and old.resolve().parent == folder:
                old.unlink()
        return target.name

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.schedule()
                self.cached = self.store.snapshot()
                self.error = None
                busy = self.server.archive_jobs.busy or self.server.brain._is_processing or self.server.event_bus.pending_dialogues
                job = None if busy else self.store.claim(self.owner)
                if job:
                    try:
                        if job['kind'] != 'maintenance.backup':
                            raise RuntimeError('未注册的服务任务')
                        result = self.backup(job)
                        self.store.complete(job['id'], self.owner, result)
                    except Exception as exc:
                        self.store.retry(job['id'], self.owner, str(exc))
                self.cached = self.store.snapshot()
            except Exception:
                self.error = '服务任务暂不可用，记录已保留等待恢复'
                logger.exception(self.error)
            self.stop_event.wait(5)

    def snapshot(self):
        return {**self.cached, 'error': self.error}


class WorldLock:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self.file.close()
            raise RuntimeError('同一世界已有运行进程，禁止多 worker 或重复启动')

    def close(self):
        self.file.close()

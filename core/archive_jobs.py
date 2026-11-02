"""Real archive phases, queryable even when the initiating connection is lost."""
import copy
import json
import threading
import uuid
from pathlib import Path


class ArchiveJobs:
    def __init__(self, manager, publish, on_restore, path='data/state/archive-job.json'):
        self.manager, self.publish, self.on_restore = manager, publish, on_restore
        self.path = Path(path)
        self.lock = threading.RLock()
        self.current = None
        self.revision = 0
        if self.path.exists():
            try:
                self.current = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(self.current, dict) or self.current.get('status') not in ('running', 'completed', 'failed'):
                    raise ValueError('Invalid task journal')
                self.revision = int(self.current.get('revision', 0))
                if self.current['status'] == 'running':
                    self.current.update(status='failed', phase='上次进程中断，请选择已知有效的存档恢复',
                        recovery_required=self.current.get('operation') in ('load', 'quick-load'))
            except (OSError, ValueError, TypeError):
                self.current = {'task_id': str(uuid.uuid4()), 'operation': 'unknown', 'status': 'failed',
                    'phase': '上次任务日志不可读，请恢复已知有效的存档', 'recovery_required': True, 'revision': 0}
            manager.recovery_required = self.current.get('recovery_required') is True

    @property
    def busy(self):
        with self.lock:
            return bool(self.current and self.current['status'] == 'running')

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.current)

    def _persist(self):
        self.revision += 1
        self.current['revision'] = self.revision
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.current, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
        return {'type': 'archive.task', 'task': self.snapshot()}

    def start(self, operation, slot='', description=''):
        if operation not in ('create', 'load', 'quick-save', 'quick-load'):
            raise ValueError('未知存档操作')
        with self.lock:
            if self.busy or self.manager._in_progress:
                raise ValueError('已有存读档任务，请等待完成')
            self.current = {'task_id': str(uuid.uuid4()), 'operation': operation, 'slot_name': slot,
                'status': 'running', 'phase': '等待当前任务结束', 'can_cancel': False,
                'result': None, 'recovery_required': False}
            if getattr(self.manager, 'event_bus', None):
                self.manager.event_bus.archive_active = True
            try:
                event = self._persist()
            except Exception:
                self.current = None
                if getattr(self.manager, 'event_bus', None):
                    self.manager.event_bus.archive_active = False
                raise
            snapshot = self.snapshot()
        self.publish(event)
        threading.Thread(target=self._run, args=(operation, slot, description), daemon=True).start()
        return snapshot

    def _phase(self, message, _percent):
        with self.lock:
            self.current['phase'] = message
            event = self._persist()
        self.publish(event)

    def _run(self, operation, slot, description):
        self.manager.set_progress_callback(self._phase)
        outcome = {}
        try:
            if operation == 'create':
                result = self.manager.create_archive(slot, description)
            elif operation == 'load':
                result = self.manager.load_archive(slot)
            else:
                result = self.manager.quick_save() if operation == 'quick-save' else self.manager.quick_load()
            outcome = {'status': 'completed' if result.success else 'failed',
                'phase': result.message, 'result': result.to_dict()}
        except Exception as exc:
            outcome = {'status': 'failed', 'phase': str(exc), 'result': {'success': False, 'error': str(exc)}}
        finally:
            self.manager.set_progress_callback(None)
            if operation in ('load', 'quick-load') and outcome.get('status') != 'completed':
                try:
                    self.on_restore()
                except Exception as exc:
                    self.manager.recovery_required = True
                    if getattr(self.manager, 'event_bus', None):
                        self.manager.event_bus.paused = True
                    outcome.update(status='failed', phase=f'重建失败，请恢复有效存档：{exc}')
            with self.lock:
                if getattr(self.manager, 'recovery_required', False):
                    outcome.update(status='failed', phase='恢复未完成，请恢复已知有效的存档', result={'success': False, 'error': '恢复需要人工处理'})
                self.current.update(outcome)
                if getattr(self.manager, 'event_bus', None):
                    self.manager.event_bus.archive_active = False
                self.current['recovery_required'] = getattr(self.manager, 'recovery_required', False)
                event = self._persist()
            self.publish(event)

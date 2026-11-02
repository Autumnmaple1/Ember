export class WorldEpoch {
  constructor() { this.epoch = null; this.retired = new Set(); this.seen = new Set(); this.cursor = 0; }
  reset(epoch) {
    if (!epoch || this.retired.has(epoch) || this.epoch === epoch) return false;
    if (this.epoch) this.retired.add(this.epoch);
    this.epoch = epoch;
    this.cursor = 0;
    this.seen.clear();
    return true;
  }
  accepts(event) {
    if (!this.epoch) return true; // Legacy capability adapter.
    if (event.epoch !== this.epoch) return false;
    if (event.event_seq && this.seen.has(event.event_seq)) return false;
    if (event.event_seq) {
      this.seen.add(event.event_seq);
      this.cursor = Math.max(this.cursor, event.event_seq);
      if (this.seen.size > 2048) this.seen.delete(this.seen.values().next().value);
    }
    return true;
  }
}

export function mergeConfigSnapshot(previous, incoming) {
  if (!previous || previous.epoch !== incoming.epoch) return incoming;
  const result = { ...incoming };
  if ((previous.state_version || 0) > (incoming.state_version || 0)) {
    result.state = previous.state;
    result.state_version = previous.state_version;
  }
  if ((previous.experience?.revision || 0) > (incoming.experience?.revision || 0)) result.experience = previous.experience;
  if ((previous.archive_task?.revision || 0) > (incoming.archive_task?.revision || 0)) result.archive_task = previous.archive_task;
  if (previous.archive_task?.task_id === incoming.archive_task?.task_id && previous.archive_task?.status !== 'running' && incoming.archive_task?.status === 'running') result.archive_task = previous.archive_task;
  return result;
}

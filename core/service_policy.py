"""Optional hosted-service accounting. Local mode has no control-plane IO."""
_store = None


def install(store):
    global _store
    _store = store


def reserve(kind, model='', call_type=''):
    return (_store, _store.reserve(kind, model, call_type)) if _store else None


def finish(ticket, usage=None, status='completed'):
    if ticket:
        # A durable reservation already limits requests even if accounting fails.
        try:
            ticket[0].finish_usage(ticket[1], status, usage)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('服务用量回执保存失败；保留原始待核对记录')

"""PostgreSQL control plane, separate from the three archived world tables."""
import hashlib
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from psycopg2.extras import Json, RealDictCursor


class BudgetExceeded(RuntimeError):
    pass


class ServiceStore:
    def __init__(self, connection=None, schema='ember_service', limits=None):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
            raise ValueError('Invalid service schema')
        if connection is None:
            from memory.postgres import connection
        self.connection, self.s = connection, schema
        self.limits = limits or {'chat': 200, 'embedding': 200, 'tts': 200}

    @contextmanager
    def cursor(self):
        with self.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur

    def initialize(self):
        with self.cursor() as c:
            c.execute(f'''CREATE SCHEMA IF NOT EXISTS {self.s};
                CREATE TABLE IF NOT EXISTS {self.s}.sessions (
                    id text PRIMARY KEY, token_hash text UNIQUE NOT NULL, credential text NOT NULL,
                    label text NOT NULL, created_at timestamptz DEFAULT now(), last_seen timestamptz DEFAULT now(),
                    expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false);
                CREATE TABLE IF NOT EXISTS {self.s}.login_attempts (
                    peer text PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(), attempts int NOT NULL);
                CREATE TABLE IF NOT EXISTS {self.s}.jobs (
                    id text PRIMARY KEY, dedupe text UNIQUE NOT NULL, kind text NOT NULL, payload jsonb NOT NULL,
                    status text NOT NULL DEFAULT 'pending', run_after timestamptz NOT NULL DEFAULT now(),
                    lease_until timestamptz, owner text, attempts int NOT NULL DEFAULT 0,
                    result jsonb, updated_at timestamptz NOT NULL DEFAULT now());
                CREATE TABLE IF NOT EXISTS {self.s}.usage (
                    id text PRIMARY KEY, kind text NOT NULL, model text NOT NULL, call_type text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(), status text NOT NULL DEFAULT 'pending',
                    input_tokens bigint NOT NULL DEFAULT 0, output_tokens bigint NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS usage_created_at ON {self.s}.usage(created_at);
                CREATE INDEX IF NOT EXISTS jobs_due ON {self.s}.jobs(status,run_after);''')

    def check_ready(self):
        """Probe a fresh transaction; initialization flags can outlive a DB outage."""
        with self.cursor() as c:
            c.execute('SELECT 1')
            c.fetchone()

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, token, credential, label, lifetime):
        with self.cursor() as c:
            c.execute(f'''INSERT INTO {self.s}.sessions(id,token_hash,credential,label,expires_at)
                VALUES(%s,%s,%s,%s,now()+(%s * interval '1 second')) RETURNING id''',
                (str(uuid.uuid4()), self.digest(token), credential, label[:80], lifetime))
            return c.fetchone()['id']

    def session(self, token, credential):
        if not token or len(token) > 200:
            return None
        with self.cursor() as c:
            c.execute(f'''SELECT id,label,expires_at FROM {self.s}.sessions
                WHERE token_hash=%s AND credential=%s AND NOT revoked AND expires_at>now()''', (self.digest(token), credential))
            result = c.fetchone()
            if result:
                c.execute(f'''UPDATE {self.s}.sessions SET last_seen=now()
                    WHERE id=%s AND last_seen<now()-interval '1 minute' ''', (result['id'],))
            return dict(result) if result else None

    def devices(self, credential):
        with self.cursor() as c:
            c.execute(f'''SELECT id,label,created_at,last_seen,expires_at FROM {self.s}.sessions
                WHERE credential=%s AND NOT revoked AND expires_at>now() ORDER BY created_at DESC''', (credential,))
            return [dict(x) for x in c.fetchall()]

    def revoke(self, key):
        with self.cursor() as c:
            c.execute(f'UPDATE {self.s}.sessions SET revoked=true WHERE id=%s', (key,))

    def login_allowed(self, peer):
        key = self.digest(peer or 'unknown')
        with self.cursor() as c:
            c.execute(f'''INSERT INTO {self.s}.login_attempts(peer,attempts) VALUES(%s,1)
                ON CONFLICT(peer) DO UPDATE SET attempts=CASE
                    WHEN {self.s}.login_attempts.started_at<now()-interval '5 minutes' THEN 1
                    ELSE {self.s}.login_attempts.attempts+1 END,
                started_at=CASE WHEN {self.s}.login_attempts.started_at<now()-interval '5 minutes'
                    THEN now() ELSE {self.s}.login_attempts.started_at END RETURNING attempts''', (key,))
            return c.fetchone()['attempts'] <= 10

    def enqueue(self, kind, dedupe, payload=None, delay=0):
        with self.cursor() as c:
            c.execute(f'''INSERT INTO {self.s}.jobs(id,kind,dedupe,payload,run_after)
                VALUES(%s,%s,%s,%s,now()+(%s*interval '1 second')) ON CONFLICT(dedupe) DO NOTHING''',
                (str(uuid.uuid4()), kind, dedupe, Json(payload or {}), delay))
            c.execute(f'SELECT * FROM {self.s}.jobs WHERE dedupe=%s', (dedupe,))
            return dict(c.fetchone())

    def claim(self, owner, lease_seconds=600):
        with self.cursor() as c:
            c.execute(f'''UPDATE {self.s}.jobs SET status=CASE WHEN attempts>=5 THEN 'failed' ELSE 'pending' END,
                owner=NULL,lease_until=NULL WHERE status='running' AND lease_until<now()''')
            c.execute(f'''SELECT id FROM {self.s}.jobs WHERE status='pending' AND run_after<=now()
                ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1''')
            row = c.fetchone()
            if not row:
                return None
            c.execute(f'''UPDATE {self.s}.jobs SET status='running',owner=%s,attempts=attempts+1,
                lease_until=now()+(%s*interval '1 second'),updated_at=now() WHERE id=%s RETURNING *''',
                (owner, lease_seconds, row['id']))
            return dict(c.fetchone())

    def complete(self, key, owner, result):
        with self.cursor() as c:
            c.execute(f'''UPDATE {self.s}.jobs SET status='completed',result=%s,lease_until=NULL,updated_at=now()
                WHERE id=%s AND owner=%s AND status='running' AND lease_until>now()''', (Json(result), key, owner))
            return c.rowcount == 1

    def retry(self, key, owner, reason):
        with self.cursor() as c:
            c.execute(f'''UPDATE {self.s}.jobs SET status=CASE WHEN attempts>=5 THEN 'failed' ELSE 'pending' END,
                result=%s,run_after=now()+interval '1 minute',lease_until=NULL,owner=NULL,updated_at=now()
                WHERE id=%s AND owner=%s AND status='running' ''', (Json({'error': reason[:200]}), key, owner))

    def reserve(self, kind, model='', call_type=''):
        if kind not in self.limits:
            raise ValueError('Unknown external call kind')
        with self.cursor() as c:
            # Reservations count even if the network fails: billing may be uncertain.
            c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (self.s + '.usage',))
            c.execute(f'''SELECT count(*) AS n FROM {self.s}.usage WHERE kind=%s
                AND created_at>=date_trunc('day',now())''', (kind,))
            if c.fetchone()['n'] >= self.limits[kind]:
                raise BudgetExceeded('今日调用额度已用完，请查看设置中的服务用量')
            key = str(uuid.uuid4())
            c.execute(f'INSERT INTO {self.s}.usage(id,kind,model,call_type) VALUES(%s,%s,%s,%s)', (key, kind, model, call_type))
            return key

    def finish_usage(self, key, status='completed', usage=None):
        def tokens(name, alternative):
            value = getattr(usage, name, None) if usage is not None else None
            if value is None and usage is not None:
                value = getattr(usage, alternative, 0)
            try:
                return max(0, int(value or 0))
            except (ValueError, TypeError):
                return 0
        with self.cursor() as c:
            c.execute(f'''UPDATE {self.s}.usage SET status=%s,input_tokens=%s,output_tokens=%s
                WHERE id=%s''', (status, tokens('prompt_tokens', 'prompt_token_count'),
                tokens('completion_tokens', 'candidates_token_count'), key))

    def snapshot(self):
        with self.cursor() as c:
            c.execute(f'''SELECT kind,count(*) AS requests,sum(input_tokens) AS input_tokens,
                sum(output_tokens) AS output_tokens FROM {self.s}.usage
                WHERE created_at>=date_trunc('day',now()) GROUP BY kind''')
            usage = {x['kind']: dict(x) for x in c.fetchall()}
            c.execute(f'''SELECT id,kind,status,updated_at,result FROM {self.s}.jobs
                ORDER BY updated_at DESC LIMIT 5''')
            jobs = [dict(x) for x in c.fetchall()]
        return {'enabled': True, 'limits': self.limits, 'usage': usage, 'jobs': jobs,
            'measured_at': datetime.now(timezone.utc).isoformat()}

"""Durable, deduplicated inbox shared by receiver instances."""

from contextlib import contextmanager
import sqlite3


class StoreError(Exception):
    pass


class EventConflict(Exception):
    pass


class EventStore:
    def __init__(self, database_url, *, allow_sqlite=False):
        self.sqlite = database_url.startswith('sqlite:///')
        if self.sqlite and not allow_sqlite:
            raise ValueError('O receptor hospedado exige PostgreSQL.')
        if not self.sqlite and not database_url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('DATABASE_URL deve apontar para PostgreSQL.')
        self.url = database_url

    @contextmanager
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        connection = None
        try:
            if self.sqlite:
                connection = sqlite3.connect(self.url.removeprefix('sqlite:///'), timeout=3)
                connection.row_factory = sqlite3.Row
            else:
                connection = psycopg.connect(self.url, connect_timeout=3,
                    options='-c statement_timeout=3000', row_factory=dict_row)
            with connection:
                yield connection
        except (sqlite3.DatabaseError, psycopg.Error):
            raise StoreError('Banco indisponível; o evento não foi confirmado.') from None
        finally:
            if connection is not None:
                connection.close()

    def execute(self, connection, sql, params=()):
        return connection.execute(sql.replace('%s', '?') if self.sqlite else sql, params)

    def initialize(self):
        identity = 'INTEGER PRIMARY KEY AUTOINCREMENT' if self.sqlite else 'BIGSERIAL PRIMARY KEY'
        timestamp = 'TEXT' if self.sqlite else 'TIMESTAMPTZ'
        with self.connect() as c:
            if not self.sqlite:
                c.execute('SELECT pg_advisory_xact_lock(720261014)')
            c.execute(f'''CREATE TABLE IF NOT EXISTS relay_events (
                sequence {identity}, event_id TEXT NOT NULL UNIQUE,
                item_id TEXT NOT NULL, event TEXT NOT NULL, triggered_by TEXT,
                received_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
                acknowledged_at {timestamp})''')
            c.execute('CREATE INDEX IF NOT EXISTS relay_pending ON relay_events(sequence) WHERE acknowledged_at IS NULL')

    def health(self):
        with self.connect() as c:
            c.execute('SELECT 1').fetchone()

    def enqueue(self, event):
        with self.connect() as c:
            inserted = self.execute(c, '''INSERT INTO relay_events(event_id,item_id,event,triggered_by)
                VALUES (%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING RETURNING event_id''',
                (event['event_id'],event['item_id'],event['event'],event['triggered_by'])).fetchone()
            if not inserted:
                existing = self.execute(c, 'SELECT item_id,event FROM relay_events WHERE event_id=%s', (event['event_id'],)).fetchone()
                if existing['item_id'] != event['item_id'] or existing['event'] != event['event']:
                    raise EventConflict('Esse eventId já identifica outro evento.')
        return bool(inserted)

    def pending(self, limit=100):
        with self.connect() as c:
            rows = self.execute(c, '''SELECT event_id,item_id,event,triggered_by,received_at
                FROM relay_events WHERE acknowledged_at IS NULL ORDER BY sequence LIMIT %s''', (limit+1,)).fetchall()
        events = [dict(r) for r in rows[:limit]]
        for event in events:
            event['received_at'] = str(event['received_at'])
        return {'events': events, 'has_more': len(rows)>limit}

    def acknowledge(self, event_ids):
        placeholders = ','.join(['%s']*len(event_ids))
        with self.connect() as c:
            rows = self.execute(c, f'''UPDATE relay_events SET acknowledged_at=CURRENT_TIMESTAMP
                WHERE acknowledged_at IS NULL AND event_id IN ({placeholders}) RETURNING event_id''',
                tuple(event_ids)).fetchall()
        return len(rows)

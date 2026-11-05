"""Small transactional config store; production is PostgreSQL, SQLite is test-only."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.rotation import UNIVERSE


class StoreError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Store:
    def __init__(self, *, dsn=None, pg_options=None, unittest_sqlite=None):
        self.dsn = dsn
        self.pg_options = pg_options or {}
        self.sqlite_path = unittest_sqlite
        self.backend = 'sqlite-unittest' if unittest_sqlite else 'postgresql'
        if not unittest_sqlite and not (dsn or self.pg_options.get('host')):
            raise RuntimeError('Configure DATABASE_URL or PGHOST/PGUSER/PGPASSWORD/PGDATABASE; '
                               'SQLite is available only through the unittest store.')
        if dsn and not dsn.startswith(('postgres://', 'postgresql://')):
            raise RuntimeError('DATABASE_URL must be a PostgreSQL URL')

    @classmethod
    def from_env(cls):
        dsn = os.getenv('DATABASE_URL')
        if dsn:
            return cls(dsn=dsn)
        options = {key: os.environ[env] for key, env in (
            ('host', 'PGHOST'), ('port', 'PGPORT'), ('user', 'PGUSER'),
            ('password', 'PGPASSWORD'), ('dbname', 'PGDATABASE'), ('sslmode', 'PGSSLMODE')
        ) if os.getenv(env)}
        return cls(pg_options=options)

    @classmethod
    def for_unittest(cls, path: Path):
        # Explicit dependency injection only: no deployment env can enable SQLite.
        return cls(unittest_sqlite=str(path))

    @contextmanager
    def transaction(self):
        if self.sqlite_path:
            conn = sqlite3.connect(self.sqlite_path, timeout=10)
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('BEGIN IMMEDIATE')
        else:
            import psycopg
            conn = psycopg.connect(self.dsn or '', **self.pg_options, connect_timeout=5)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, conn, sql, params=()):
        return conn.execute(sql.replace('?', '%s') if not self.sqlite_path else sql, params)

    def lock(self, conn):
        suffix = '' if self.sqlite_path else ' FOR UPDATE'
        return self.execute(conn, 'SELECT active_group_id, revision FROM sector_map_config '
                            'WHERE id = 1' + suffix).fetchone()

    def initialize(self):
        with self.transaction() as conn:
            for sql in (
                '''CREATE TABLE IF NOT EXISTS sector_map_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1), active_group_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0)''',
                '''CREATE TABLE IF NOT EXISTS sector_map_groups (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, position INTEGER NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS sector_map_tickers (
                    group_id TEXT NOT NULL REFERENCES sector_map_groups(id) ON DELETE CASCADE,
                    ticker TEXT NOT NULL, position INTEGER NOT NULL,
                    PRIMARY KEY (group_id, ticker))''',
                '''CREATE TABLE IF NOT EXISTS sector_map_templates (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)''',
            ):
                self.execute(conn, sql)
            self.execute(conn, "INSERT INTO sector_map_config (id, active_group_id) VALUES (1, '') "
                         'ON CONFLICT (id) DO NOTHING')
            active, _ = self.lock(conn)
            if not active:
                first, second = str(uuid4()), str(uuid4())
                self.write(conn, {'groups': [
                    {'id': first, 'name': 'iShare Sector', 'tickers': list(UNIVERSE)},
                    {'id': second, 'name': 'Sector ETF', 'tickers': []},
                ], 'active_id': first})

    def read(self, conn):
        active, revision = self.execute(conn, 'SELECT active_group_id, revision '
                                       'FROM sector_map_config WHERE id = 1').fetchone()
        groups = [{'id': row[0], 'name': row[1], 'tickers': []} for row in self.execute(
            conn, 'SELECT id, name FROM sector_map_groups ORDER BY position').fetchall()]
        by_id = {g['id']: g for g in groups}
        for group_id, ticker in self.execute(
                conn, 'SELECT group_id, ticker FROM sector_map_tickers ORDER BY position').fetchall():
            by_id[group_id]['tickers'].append(ticker)
        return {'groups': groups, 'active_id': active, 'revision': revision}

    def snapshot(self):
        with self.transaction() as conn:
            self.lock(conn)
            return self.read(conn)

    def write(self, conn, snapshot):
        self.execute(conn, 'DELETE FROM sector_map_tickers')
        self.execute(conn, 'DELETE FROM sector_map_groups')
        for position, group in enumerate(snapshot['groups']):
            self.execute(conn, 'INSERT INTO sector_map_groups (id, name, position) VALUES (?, ?, ?)',
                         (group['id'], group['name'], position))
            for index, ticker in enumerate(group['tickers']):
                self.execute(conn, 'INSERT INTO sector_map_tickers (group_id, ticker, position) '
                             'VALUES (?, ?, ?)', (group['id'], ticker, index))
        self.execute(conn, 'UPDATE sector_map_config SET active_group_id = ?, '
                     'revision = revision + 1 WHERE id = 1', (snapshot['active_id'],))

    def mutate(self, change):
        with self.transaction() as conn:
            self.lock(conn)
            snapshot = self.read(conn)
            change(snapshot)
            self.write(conn, snapshot)
            return self.read(conn)

    def replace(self, snapshot):
        # The bulk editor uses a revision to reject stale saves from another tab.
        with self.transaction() as conn:
            self.lock(conn)
            current = self.read(conn)
            if snapshot['revision'] != current['revision']:
                raise StoreError(409, 'Groups changed in another tab. Reload groups and try again.')
            self.write(conn, snapshot)
            return self.read(conn)

    @staticmethod
    def group(snapshot, group_id):
        for group in snapshot['groups']:
            if group['id'] == group_id:
                return group
        raise StoreError(404, 'Group not found')

    @staticmethod
    def unique_name(snapshot, name, except_id=None):
        if any(g['name'].casefold() == name.casefold() and g['id'] != except_id
               for g in snapshot['groups']):
            raise StoreError(409, 'A group with that name already exists')

    def create_group(self, name):
        def change(snapshot):
            self.unique_name(snapshot, name)
            snapshot['groups'].append({'id': str(uuid4()), 'name': name, 'tickers': []})
        return self.mutate(change)

    def rename_group(self, group_id, name):
        def change(snapshot):
            group = self.group(snapshot, group_id)
            self.unique_name(snapshot, name, group_id)
            group['name'] = name
        return self.mutate(change)

    def delete_group(self, group_id):
        def change(snapshot):
            group = self.group(snapshot, group_id)
            if len(snapshot['groups']) == 1:
                raise StoreError(409, 'Keep at least one group')
            snapshot['groups'].remove(group)
            if snapshot['active_id'] == group_id:
                snapshot['active_id'] = snapshot['groups'][0]['id']
        return self.mutate(change)

    def activate(self, group_id):
        def change(snapshot):
            self.group(snapshot, group_id)
            snapshot['active_id'] = group_id
        return self.mutate(change)

    def add_ticker(self, group_id, ticker):
        def change(snapshot):
            group = self.group(snapshot, group_id)
            if ticker in group['tickers']:
                raise StoreError(409, 'Ticker already belongs to this group')
            group['tickers'].append(ticker)
        return self.mutate(change)

    def remove_ticker(self, group_id, ticker):
        def change(snapshot):
            group = self.group(snapshot, group_id)
            if ticker not in group['tickers']:
                raise StoreError(404, 'Ticker not found in this group')
            group['tickers'].remove(ticker)
        return self.mutate(change)

    def templates(self):
        with self.transaction() as conn:
            return [{'id': r[0], 'name': r[1], 'updated_at': r[2]} for r in self.execute(
                conn, 'SELECT id, name, updated_at FROM sector_map_templates ORDER BY name').fetchall()]

    def save_template(self, name=None, template_id=None):
        with self.transaction() as conn:
            self.lock(conn)
            snapshot = self.read(conn)
            snapshot.pop('revision')
            now = datetime.now(timezone.utc).isoformat()
            if template_id:
                if not self.execute(conn, 'SELECT id FROM sector_map_templates WHERE id = ?',
                                    (template_id,)).fetchone():
                    raise StoreError(404, 'Template not found')
                self.execute(conn, 'UPDATE sector_map_templates SET snapshot = ?, updated_at = ? '
                             'WHERE id = ?', (json.dumps(snapshot), now, template_id))
            else:
                names = self.execute(conn, 'SELECT name FROM sector_map_templates').fetchall()
                if any(r[0].casefold() == name.casefold() for r in names):
                    raise StoreError(409, 'Template exists; use overwrite to replace it')
                template_id = str(uuid4())
                self.execute(conn, 'INSERT INTO sector_map_templates '
                             '(id, name, snapshot, updated_at) VALUES (?, ?, ?, ?)',
                             (template_id, name, json.dumps(snapshot), now))
        return {'id': template_id, 'updated_at': now}

    def load_template(self, template_id):
        with self.transaction() as conn:
            self.lock(conn)
            row = self.execute(conn, 'SELECT snapshot FROM sector_map_templates WHERE id = ?',
                               (template_id,)).fetchone()
            if not row:
                raise StoreError(404, 'Template not found')
            self.write(conn, json.loads(row[0]))
            return self.read(conn)

    def delete_template(self, template_id):
        with self.transaction() as conn:
            self.lock(conn)
            result = self.execute(conn, 'DELETE FROM sector_map_templates WHERE id = ?', (template_id,))
            if result.rowcount == 0:
                raise StoreError(404, 'Template not found')

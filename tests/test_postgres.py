"""Opt-in: run the persistence suite against a disposable PostgreSQL schema.

Set SECTOR_MAP_TEST_DATABASE_URL to a test database with CREATE SCHEMA rights.
Never uses DATABASE_URL or modifies the deployment's sector_map_* tables.
"""
import os
import unittest
from uuid import uuid4

from app.storage import Store
import test_groups


@unittest.skipUnless(os.getenv('SECTOR_MAP_TEST_DATABASE_URL'), 'No PostgreSQL test URL configured')
class PostgresPersistenceTests(test_groups.GroupPersistenceTests):
    def setUp(self):
        import psycopg
        from psycopg import sql

        dsn = os.environ['SECTOR_MAP_TEST_DATABASE_URL']
        schema = 'sector_map_test_' + uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

        def cleanup():
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

        self.addCleanup(cleanup)
        self.store = Store(dsn=dsn, pg_options={'options':f'-c search_path={schema}'})
        self.store.initialize()
        # Reopen tests use this factory with the same schema instead of a test file.
        self.path = schema
        self._factory_patch = unittest.mock.patch.object(
            Store, 'for_unittest', side_effect=lambda path: Store(
                dsn=dsn, pg_options={'options':f'-c search_path={schema}'}))
        self._factory_patch.start()
        self.addCleanup(self._factory_patch.stop)

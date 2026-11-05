from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.groups import GroupsConfig
from app.rotation import UNIVERSE
from app.storage import Store, StoreError


class GroupPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'groups.sqlite3'
        self.store = Store.for_unittest(self.path)
        self.store.initialize()

    def test_defaults_and_idempotent_startup(self):
        before = self.store.snapshot()
        self.store.initialize()
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual([g['name'] for g in before['groups']], ['iShare Sector', 'Sector ETF'])
        self.assertEqual(before['groups'][0]['tickers'], UNIVERSE)
        self.assertEqual(before['groups'][1]['tickers'], [])
        self.assertEqual(before['active_id'], before['groups'][0]['id'])

    def test_free_tickers_group_lifecycle_and_restart(self):
        snapshot = self.store.create_group('Karson')
        group_id = snapshot['groups'][-1]['id']
        for ticker in ['MAGS', 'AIPO', 'DRAM', 'AAPL', 'BRK-B']:
            self.store.add_ticker(group_id, ticker)
        self.store.activate(group_id)
        self.store.rename_group(group_id, 'Macro')
        self.store.remove_ticker(group_id, 'AAPL')
        reopened = Store.for_unittest(self.path)
        reopened.initialize()
        snapshot = reopened.snapshot()
        self.assertEqual(snapshot['active_id'], group_id)
        self.assertEqual(snapshot['groups'][-1]['name'], 'Macro')
        self.assertEqual(snapshot['groups'][-1]['tickers'], ['MAGS', 'AIPO', 'DRAM', 'BRK-B'])
        after = reopened.delete_group(group_id)
        self.assertEqual(after['active_id'], after['groups'][0]['id'])
        reopened.delete_group(after['groups'][-1]['id'])
        with self.assertRaises(StoreError):
            reopened.delete_group(after['groups'][0]['id'])

    def test_validation_and_normalization(self):
        before = self.store.snapshot()
        normalized = GroupsConfig.model_validate({**before, 'groups': [
            {'id':'a', 'name':' Free tickers ', 'tickers':[' mags ', 'aipo', 'dram', 'BRK-B']}
        ], 'active_id':'a'})
        self.assertEqual(normalized.active.tickers, ['MAGS', 'AIPO', 'DRAM', 'BRK-B'])
        self.assertEqual(normalized.active.name, 'Free tickers')
        cases = [
            {'active_id':'missing'}, {'groups':[]},
            {'groups':[{'id':'a', 'name':' ', 'tickers':[]}]},
            {'groups':[{'id':'a', 'name':'Test', 'tickers':['bad/symbol']}]},
            {'groups':[{'id':'a', 'name':'Test', 'tickers':['mags', 'MAGS']}]},
            {'groups':[{'id':'a', 'name':'Test'}, {'id':'a', 'name':'Other'}]},
            {'groups':[{'id':'a', 'name':'Test'}, {'id':'b', 'name':' test '}]},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                GroupsConfig.model_validate({**before, **changes})
        self.assertEqual(self.store.snapshot(), before)

    def test_atomic_conflicting_bulk_edits(self):
        before = self.store.snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.store.replace, before) for _ in range(2)]
        results, errors = [], []
        for future in futures:
            try:
                results.append(future.result())
            except StoreError as exc:
                errors.append(exc)
        self.assertEqual(len(results), 1)
        self.assertEqual([e.status for e in errors], [409])
        self.assertEqual(self.store.snapshot()['revision'], before['revision'] + 1)

    def test_template_roundtrip_overwrite_delete_and_restart(self):
        original = self.store.snapshot()
        template = self.store.save_template(name='Morning brief')
        self.store.activate(original['groups'][1]['id'])
        self.store.create_group('Temporary')
        restored = self.store.load_template(template['id'])
        self.assertEqual(restored['groups'], original['groups'])
        self.assertEqual(restored['active_id'], original['active_id'])
        self.assertGreater(restored['revision'], original['revision'])
        self.store.add_ticker(restored['groups'][1]['id'], 'MAGS')
        self.store.activate(restored['groups'][1]['id'])
        replacement = self.store.snapshot()
        self.store.save_template(template_id=template['id'])
        self.store.delete_group(replacement['active_id'])
        reopened = Store.for_unittest(self.path)
        reopened.initialize()
        restored = reopened.load_template(template['id'])
        self.assertEqual(restored['groups'], replacement['groups'])
        self.assertEqual(restored['active_id'], replacement['active_id'])
        with self.assertRaises(StoreError) as error:
            reopened.save_template(name=' morning BRIEF '.strip())
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(reopened.templates()), 1)
        reopened.delete_template(template['id'])
        self.assertEqual(reopened.templates(), [])
        for action in [reopened.load_template, reopened.delete_template,
                       lambda template_id: reopened.save_template(template_id=template_id)]:
            with self.assertRaises(StoreError) as error:
                action(template['id'])
            self.assertEqual(error.exception.status, 404)

    def test_failed_changes_rollback_without_revision_increment(self):
        before = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.rename_group(before['groups'][1]['id'], 'iShare Sector')
        with self.assertRaises(StoreError):
            self.store.add_ticker(before['active_id'], 'XLK')
        self.assertEqual(self.store.snapshot(), before)


class DeploymentConfigTests(unittest.TestCase):
    def test_sqlite_is_never_selected_by_deployment_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Configure DATABASE_URL'):
                Store.from_env()
        with patch.dict(os.environ, {'DATABASE_URL':'sqlite:///config.db'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'PostgreSQL URL'):
                Store.from_env()

    def test_postgres_url_and_pg_variables(self):
        with patch.dict(os.environ, {'DATABASE_URL':'postgresql://db/app', 'PGHOST':'ignored'}, clear=True):
            store = Store.from_env()
            self.assertEqual(store.backend, 'postgresql')
            self.assertEqual(store.pg_options, {})
        with patch.dict(os.environ, {'PGHOST':'social-db', 'PGUSER':'sector',
                                   'PGPASSWORD':'secret', 'PGDATABASE':'social', 'PGPORT':'5432'}, clear=True):
            store = Store.from_env()
            self.assertEqual(store.backend, 'postgresql')
            self.assertEqual(store.pg_options['host'], 'social-db')
            self.assertEqual(store.pg_options['dbname'], 'social')

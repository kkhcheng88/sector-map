"""Exercise ASGI directly: no HTTP client, external DB, or Yahoo required."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from app.main import create_app, fetch
from app.rotation import calculate
from app.storage import Store

# Match Uvicorn's Linux event loop when its standard extra is installed.
try:
    import uvloop
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def fixture(tickers):
    prices = pd.DataFrame(100., index=pd.bdate_range('2020-01-01', periods=400),
                          columns=list(dict.fromkeys(['SPY', *tickers])))
    return calculate(prices, tickers)


async def request(app, method, path, body=None, authorization='', root_path=''):
    messages, sent = [], False
    response_complete = asyncio.Event()
    payload = json.dumps(body).encode() if body is not None else b''
    parsed = urlsplit(path)
    scope = {'type':'http', 'asgi':{'version':'3.0'}, 'http_version':'1.1',
             'method':method, 'scheme':'http', 'path':parsed.path, 'raw_path':parsed.path.encode(),
             'query_string':parsed.query.encode(), 'root_path':root_path,
             'headers':[(b'content-type', b'application/json'),
                        (b'authorization', authorization.encode())],
             'client':('127.0.0.1',12345), 'server':('testserver',80)}

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type':'http.request', 'body':payload, 'more_body':False}
        # Keep the client connected while StaticFiles streams its response.
        await response_complete.wait()
        return {'type':'http.disconnect'}

    async def send(message):
        messages.append(message)
        if message['type'] == 'http.response.body' and not message.get('more_body', False):
            response_complete.set()

    await app(scope, receive, send)
    status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return status, json.loads(content) if '/api/' in path and content else content.decode()


class GroupAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.store = Store.for_unittest(Path(self.temp.name) / 'groups.sqlite3')
        self.fetcher = Mock(side_effect=fixture)
        self.enterContext(patch.dict('os.environ', {'SECTOR_MAP_TOKEN':'', 'SECTOR_MAP_BASE':''}))
        self.app = create_app(store=self.store, fetcher=self.fetcher, start_scheduler=False)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.addAsyncCleanup(self.lifespan.__aexit__, None, None, None)
        self.cache = self.app.state.cache

    async def req(self, method, path, body=None, authorization='', root_path=''):
        return await request(self.app, method, path, body, authorization, root_path)

    async def custom_config(self):
        config = self.store.snapshot()
        config['groups'].append({'id':'custom', 'name':'Custom', 'tickers':['MAGS', 'AIPO', 'DRAM']})
        config['active_id'] = 'custom'
        code, result = await self.req('PUT', '/api/groups', config)
        self.assertEqual(code, 200)
        return result

    async def test_public_get_and_auth_for_every_mutation(self):
        await self.cache.refresh()
        config = self.store.snapshot()
        template_id = self.store.save_template(name='Test')['id']
        mutations = [('/api/groups', 'PUT', config), ('/api/refresh', 'POST', None),
                     ('/api/templates', 'POST', {'name':'Another'}),
                     (f'/api/templates/{template_id}', 'PUT', None),
                     (f'/api/templates/{template_id}/load', 'POST', None),
                     (f'/api/templates/{template_id}', 'DELETE', None)]
        with patch.dict('os.environ', {'SECTOR_MAP_TOKEN':'secret'}):
            self.assertTrue((await self.req('GET', '/api/groups'))[1]['auth_required'])
            for path in ['/api/groups', '/api/templates', '/api/healthz', '/api/rotation', '/api/summary', '/api/patterns']:
                self.assertEqual((await self.req('GET', path))[0], 200)
            for authorization in ['', 'Bearer wrong', 'Bearer invalid-☃']:
                for path, method, body in mutations:
                    self.assertEqual((await self.req(method, path, body, authorization))[0], 401)
            for path, method, body in mutations:
                self.assertIn((await self.req(method, path, body, 'Bearer secret'))[0], [200, 201, 202, 204])
        self.assertEqual((await self.req('POST', '/api/refresh'))[0], 202)

    async def test_active_universe_and_summary_use_matching_snapshot(self):
        config = await self.custom_config()
        self.assertTrue(self.cache.requested.is_set())
        await self.cache.refresh()
        self.fetcher.assert_called_once_with(['MAGS', 'AIPO', 'DRAM'])
        code, payload = await self.req('GET', '/api/rotation')
        self.assertEqual(code, 200)
        self.assertEqual(set(payload['trails']), {'MAGS', 'AIPO', 'DRAM'})
        self.assertEqual(payload['benchmark'], 'SPY')
        code, summary = await self.req('GET', '/api/summary')
        self.assertEqual(code, 200)
        self.assertEqual(summary['config_revision'], config['revision'])
        self.assertEqual(summary['as_of'], payload['as_of'])
        self.assertEqual(summary['group'], payload['group'])
        self.assertEqual(summary['snapshot_id'], payload['snapshot_id'])
        self.assertIn('MAGS', summary['text'])
        self.assertEqual(summary['text'].count('👉'), 1)
        self.assertEqual(summary, (await self.req('GET', '/api/summary'))[1])

    async def test_validation_conflicts_and_cache_invalidation(self):
        config = self.store.snapshot()
        self.assertEqual((await self.req('PUT', '/api/groups', {**config, 'active_id':'missing'}))[0], 422)
        await self.cache.refresh()
        await self.custom_config()
        self.assertEqual((await self.req('PUT', '/api/groups', config))[0], 409)
        for path in ['/api/rotation', '/api/summary']:
            self.assertEqual((await self.req('GET', path))[0], 503)
        self.assertFalse((await self.req('GET', '/api/healthz'))[1]['ready'])

    async def test_empty_group_needs_no_download(self):
        config = self.store.snapshot()
        config['active_id'] = config['groups'][1]['id']
        self.assertEqual((await self.req('PUT', '/api/groups', config))[0], 200)
        await self.cache.refresh()
        self.fetcher.assert_not_called()
        code, payload = await self.req('GET', '/api/rotation')
        self.assertEqual(code, 200)
        self.assertEqual(payload['trails'], {})
        self.assertIsNone(payload['as_of'])
        self.assertTrue((await self.req('GET', '/api/healthz'))[1]['ready'])
        self.assertIn('未有可用', (await self.req('GET', '/api/summary'))[1]['text'])
        self.assertIn('未有可用軌跡', (await self.req('GET', '/api/patterns?trail_length=504'))[1]['text'])

    async def test_failed_refresh_keeps_matching_snapshot_and_marks_summary_stale(self):
        await self.cache.refresh()
        old = self.cache.state['data']
        self.fetcher.side_effect = ValueError('upstream down')
        with self.assertLogs('app.main', level='ERROR'):
            await self.cache.refresh()
        code, payload = await self.req('GET', '/api/rotation')
        self.assertEqual(code, 200)
        self.assertTrue(payload['stale'])
        self.assertEqual(payload['trails'], old['trails'])
        summary = (await self.req('GET', '/api/summary'))[1]
        self.assertTrue(summary['stale'])
        self.assertIn('上次成功數據', summary['text'])
        patterns = (await self.req('GET', '/api/patterns'))[1]
        self.assertTrue(patterns['stale'])
        self.assertEqual(patterns['snapshot_id'], payload['snapshot_id'])
        self.assertIn('上次成功數據', patterns['text'])
        await self.custom_config()
        self.assertIsNone(self.cache.state['data'])
        self.assertIsNone(self.cache.state['error'])

    async def test_universe_change_during_download_discards_old_data(self):
        started, release = threading.Event(), threading.Event()

        def slow_fetch(tickers):
            started.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release download')
            return fixture(tickers)

        self.fetcher.side_effect = slow_fetch
        task = asyncio.create_task(self.cache.refresh())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            await self.custom_config()
        finally:
            release.set()
            await task
        self.assertIsNone(self.cache.state['data'])
        self.fetcher.side_effect = fixture
        await self.cache.refresh()
        self.assertEqual(set(self.cache.state['data']['trails']), {'MAGS', 'AIPO', 'DRAM'})

    async def test_scheduler_wakes_on_group_changes(self):
        fetched, calls = asyncio.Event(), []
        loop = asyncio.get_running_loop()

        def download(tickers):
            calls.append(tickers)
            result = fixture(tickers)
            loop.call_soon_threadsafe(fetched.set)
            return result

        self.fetcher.side_effect = download
        task = asyncio.create_task(self.cache.scheduler())
        try:
            await asyncio.wait_for(fetched.wait(), 2)
            fetched.clear()
            await self.custom_config()
            await asyncio.wait_for(fetched.wait(), 2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(calls[-1], ['MAGS', 'AIPO', 'DRAM'])

    async def test_templates_api_restore_complete_setup(self):
        before = self.store.snapshot()
        code, saved = await self.req('POST', '/api/templates', {'name':'Morning'})
        self.assertEqual(code, 201)
        self.assertEqual((await self.req('POST', '/api/templates', {'name':'morning'}))[0], 409)
        self.assertEqual((await self.req('POST', '/api/templates', {'name':' '}))[0], 422)
        self.assertEqual((await self.req('GET', '/api/templates'))[1]['templates'][0]['id'], saved['id'])
        await self.custom_config()
        code, restored = await self.req('POST', f"/api/templates/{saved['id']}/load")
        self.assertEqual(code, 200)
        self.assertEqual(restored['groups'], before['groups'])
        self.assertEqual(restored['active_id'], before['active_id'])
        self.assertEqual((await self.req('PUT', f"/api/templates/{saved['id']}"))[0], 200)
        self.assertEqual((await self.req('DELETE', f"/api/templates/{saved['id']}"))[0], 204)
        self.assertEqual((await self.req('GET', '/api/templates'))[1], {'templates':[]})

    async def test_stripped_prefix_assets_and_public_api(self):
        with patch.dict('os.environ', {'SECTOR_MAP_BASE':'/sectors'}):
            app = create_app(store=self.store, fetcher=self.fetcher, start_scheduler=False)
        # Traefik removes /sectors before forwarding; ASGI root_path stays empty.
        async with app.router.lifespan_context(app):
            self.assertEqual(app.root_path, '')
            code, html = await request(app, 'GET', '/')
            self.assertEqual(code, 200)
            self.assertIn('src="/sectors/static/app.js"', html)
            self.assertIn('href="/sectors/static/styles.css"', html)
            self.assertNotIn('__BASE__', html)
            self.assertNotIn('__PUBLIC_BASE__', html)
            self.assertIn('<base href="/sectors/">', html)
            for path in ['/api/groups', '/api/healthz', '/static/styles.css', '/static/app.js']:
                code, content = await request(app, 'GET', path)
                self.assertEqual(code, 200, path)
                if path.startswith('/static/'):
                    asset = Path(__file__).resolve().parents[1] / 'app' / path.lstrip('/')
                    self.assertEqual(content, asset.read_text(), path)

    async def test_direct_assets_and_public_api(self):
        code, html = await self.req('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn('<base href="/">', html)
        self.assertIn('href="/static/styles.css"', html)
        self.assertIn('src="/static/app.js"', html)
        for path in ['/static/styles.css', '/static/app.js']:
            self.assertEqual((await self.req('GET', path))[0], 200, path)
        self.assertEqual((await self.req('GET', '/api/healthz'))[1]['storage'], 'sqlite-unittest')

    async def test_summary_snapshot_id_detects_same_date_corrections(self):
        await self.cache.refresh()
        first = (await self.req('GET', '/api/summary'))[1]
        self.cache.state['data']['trails']['XLK'][-1]['X'] += .25
        second = (await self.req('GET', '/api/summary'))[1]
        self.assertEqual(first['as_of'], second['as_of'])
        self.assertNotEqual(first['snapshot_id'], second['snapshot_id'])

    async def test_fetch_requests_only_benchmark_and_active_tickers(self):
        dates = pd.bdate_range('2020-01-01', periods=400)
        raw = pd.DataFrame(100., index=dates, columns=pd.MultiIndex.from_product([['Adj Close'], ['SPY', 'MAGS']]))
        with patch('app.main.yf.download', return_value=raw) as download:
            result = await asyncio.to_thread(fetch, ['MAGS'])
        self.assertEqual(download.call_args.args[0], ['SPY', 'MAGS'])
        self.assertEqual(set(result['trails']), {'MAGS'})
        self.assertEqual(len(result['trails']['MAGS']), 119)  # Full cache history, not the default ten.

    async def test_windows_patterns_summary_and_cache_share_snapshot(self):
        def long_fixture(tickers):
            prices = pd.DataFrame(100., index=pd.bdate_range('2020-01-01', periods=1000),
                                  columns=['SPY', *tickers])
            return calculate(prices, tickers, trail_length=504)
        self.fetcher.side_effect = long_fixture
        await self.cache.refresh()
        snapshots = set()
        for length in [10,63,126,252,504]:
            payloads = []
            for route in ['rotation','patterns','summary']:
                code, payload = await self.req('GET', f'/api/{route}?trail_length={length}')
                self.assertEqual(code, 200)
                payloads.append(payload)
            rotation, patterns, summary = payloads
            self.assertEqual(len(rotation['trails']['XLK']), length)
            self.assertEqual(patterns['patterns']['XLK']['sessions'], length)
            self.assertEqual(summary['snapshot_id'], rotation['snapshot_id'])
            self.assertEqual(patterns['snapshot_id'], rotation['snapshot_id'])
            snapshots.add(rotation['snapshot_id'])
        self.assertEqual(len(snapshots), 5)
        self.assertEqual(self.fetcher.call_count, 1)
        for route in ['rotation','patterns','summary']:
            for invalid in ['0','-1','11','505','word','10.5']:
                self.assertEqual((await self.req('GET', f'/api/{route}?trail_length={invalid}'))[0], 422)
        self.assertEqual(len(self.cache.state['data']['trails']['XLK']), 504)

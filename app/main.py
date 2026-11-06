import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import hashlib
import json
import os
from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
import pandas as pd
import yfinance as yf

from app.groups import GroupsConfig
from app.rotation import TRAIL_LENGTHS, calculate, empty_map, select_trails
from app.patterns import generate_patterns
from app.storage import Store, StoreError
from app.summary import generate_summary

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / 'static'


def fetch(tickers):
    raw = yf.download(list(dict.fromkeys(['SPY', *tickers])), period='5y', auto_adjust=False,
                      progress=False, threads=True, timeout=20)
    if raw is None or raw.empty or 'Adj Close' not in raw:
        raise ValueError('No adjusted-close prices returned; please retry later')
    prices = raw['Adj Close']
    # Never incorporate today's potentially incomplete US trading session.
    today = pd.Timestamp.now(tz='America/New_York')
    if (today.hour, today.minute) < (16, 15):
        prices = prices.loc[prices.index.date < today.date()]
    return calculate(prices, tickers, trail_length=504)


def require_mutation_auth(authorization: str = Header(default='')):
    token = os.getenv('SECTOR_MAP_TOKEN')
    if token and not secrets.compare_digest(authorization.encode(), f'Bearer {token}'.encode()):
        raise HTTPException(401, 'A valid management token is required',
                            headers={'WWW-Authenticate': 'Bearer'})


class TemplateName(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=60)

    @field_validator('name')
    @classmethod
    def visible_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Use a visible, single-line name')
        return value


class PriceCache:
    def __init__(self, store, fetcher):
        self.store, self.fetcher = store, fetcher
        self.lock = asyncio.Lock()
        self.requested = asyncio.Event()
        self.state = {'data': None, 'key': None, 'last_success': None, 'last_attempt': None,
                      'error': None, 'refreshing': False}

    def align(self, config):
        if self.state['key'] != config.universe_key:
            self.state.update(data=None, key=config.universe_key, last_success=None,
                              last_attempt=None, error=None)
            self.requested.set()

    async def config(self):
        return GroupsConfig.model_validate(await asyncio.to_thread(self.store.snapshot))

    async def refresh(self):
        async with self.lock:
            config = await self.config()
            self.align(config)
            self.state['last_attempt'] = datetime.now(timezone.utc).isoformat()
            self.state['refreshing'] = True
            try:
                data = (await asyncio.to_thread(self.fetcher, config.active.tickers)
                        if config.active.tickers else empty_map())
                current = await self.config()
                if current.universe_key == config.universe_key:
                    self.state.update(data=data, last_success=datetime.now(timezone.utc).isoformat(),
                                      error=None)
                else:
                    self.align(current)
                    self.requested.set()
            except Exception:
                log.exception('Price refresh failed')
                current = await self.config()
                if current.universe_key == config.universe_key:
                    self.state['error'] = 'Price refresh failed; retrying every 15 minutes'
                else:
                    self.align(current)
            finally:
                self.state['refreshing'] = False

    async def scheduler(self):
        while True:
            self.requested.clear()
            try:
                await self.refresh()
            except Exception:
                # DB outages must not silently kill the 15-minute loop.
                log.exception('Scheduled refresh failed')
            try:
                await asyncio.wait_for(self.requested.wait(), timeout=900)
            except asyncio.TimeoutError:
                pass

    async def rotation(self, trail_length=10):
        config = await self.config()
        self.align(config)
        data = self.state['data'] if config.active.tickers else empty_map()
        if data is None:
            raise HTTPException(503, self.state['error'] or 'Fetching initial prices; please retry shortly',
                                headers={'Retry-After': '15'})
        result = {**select_trails(data, trail_length), 'group': config.active.model_dump(), 'config_revision': config.revision,
                  'last_success': self.state['last_success'], 'refreshing': self.state['refreshing'],
                  'stale': self.state['error'] is not None, 'warning': self.state['error']}
        # Bind the brief to the exact map, including same-date price corrections.
        result['snapshot_id'] = hashlib.sha256(json.dumps(
            {k: v for k, v in result.items() if k not in ('last_success', 'refreshing')},
            sort_keys=True).encode()).hexdigest()
        return result


def create_app(*, store=None, fetcher=fetch, start_scheduler=True):
    @asynccontextmanager
    async def lifespan(application):
        application.state.store = store or Store.from_env()
        await asyncio.to_thread(application.state.store.initialize)
        application.state.cache = PriceCache(application.state.store, fetcher)
        application.state.cache.align(await application.state.cache.config())
        application.state.cache.requested.clear()
        task = asyncio.create_task(application.state.cache.scheduler()) if start_scheduler else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # Public HTML URLs only: Traefik StripPrefix already removed this prefix
    # from incoming paths. Keep root_path unset and route those paths unchanged.
    base = os.getenv('SECTOR_MAP_BASE', '').strip('/')
    public_base = '/' + base if base else ''
    app = FastAPI(title='US Sector Rotation', lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    auth = [Depends(require_mutation_auth)]

    @app.exception_handler(StoreError)
    async def store_error(request: Request, exc: StoreError):
        return JSONResponse({'detail': str(exc)}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def unavailable(request: Request, exc: Exception):
        log.error('Request failed: %s', type(exc).__name__)
        return JSONResponse({'detail': 'Service temporarily unavailable; please retry'}, status_code=503)

    async def config_response(snapshot=None):
        if snapshot is None:
            snapshot = await asyncio.to_thread(app.state.store.snapshot)
        return {**snapshot, 'auth_required': bool(os.getenv('SECTOR_MAP_TOKEN'))}

    async def changed(snapshot):
        app.state.cache.align(GroupsConfig.model_validate(snapshot))
        app.state.cache.requested.set()
        return await config_response(snapshot)

    @app.get('/api/healthz')
    async def health():
        config = await app.state.cache.config()
        app.state.cache.align(config)
        state = app.state.cache.state
        return {'status': 'ok', 'storage': app.state.store.backend,
                'ready': state['data'] is not None or not config.active.tickers,
                'active_group_id': config.active_id, 'refreshing': state['refreshing'],
                'last_success': state['last_success'], 'last_attempt': state['last_attempt'],
                'error': state['error']}

    def window(trail_length: int = Query(default=10)):
        if trail_length not in TRAIL_LENGTHS:
            raise HTTPException(422, 'trail_length must be 10, 63, 126, 252 or 504 sessions')
        return trail_length

    @app.get('/api/rotation')
    async def rotation(trail_length: int = Depends(window)):
        return await app.state.cache.rotation(trail_length)

    @app.get('/api/summary')
    async def summary(trail_length: int = Depends(window)):
        return generate_summary(await app.state.cache.rotation(trail_length))

    @app.get('/api/patterns')
    async def patterns(trail_length: int = Depends(window)):
        return generate_patterns(await app.state.cache.rotation(trail_length))

    @app.get('/api/groups')
    async def get_groups():
        return await config_response()

    @app.put('/api/groups', dependencies=auth)
    async def replace_groups(config: GroupsConfig):
        saved = await asyncio.to_thread(app.state.store.replace, config.model_dump())
        return await changed(saved)

    @app.get('/api/templates')
    async def templates():
        return {'templates': await asyncio.to_thread(app.state.store.templates)}

    @app.post('/api/templates', status_code=201, dependencies=auth)
    async def save_template(payload: TemplateName):
        return await asyncio.to_thread(app.state.store.save_template, name=payload.name)

    @app.put('/api/templates/{template_id}', dependencies=auth)
    async def overwrite_template(template_id: str):
        return await asyncio.to_thread(app.state.store.save_template, template_id=template_id)

    @app.post('/api/templates/{template_id}/load', dependencies=auth)
    async def load_template(template_id: str):
        return await changed(await asyncio.to_thread(app.state.store.load_template, template_id))

    @app.delete('/api/templates/{template_id}', status_code=204, dependencies=auth)
    async def delete_template(template_id: str):
        await asyncio.to_thread(app.state.store.delete_template, template_id)

    @app.post('/api/refresh', status_code=202, dependencies=auth)
    async def admin_refresh():
        app.state.cache.requested.set()
        return await health()

    @app.get('/')
    async def index():
        return HTMLResponse((STATIC / 'index.html').read_text().replace(
            '__PUBLIC_BASE__', public_base).replace('__BASE__', public_base))

    return app


app = create_app()

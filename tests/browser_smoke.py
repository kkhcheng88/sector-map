"""Optional real-browser check: python tests/browser_smoke.py (Chrome + websockets).

Uses an explicitly injected unittest SQLite store and fixture prices, never Yahoo or
production database settings. All profiles, database files, and screenshots stay
inside this workspace. This is not a development/production server entry point.
"""
import argparse
import asyncio
import base64
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def available_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def serve(port):
    import pandas as pd
    import uvicorn
    from app.main import create_app
    from app.rotation import empty_map
    from app.storage import Store

    reference = json.loads((ROOT / 'rotation-trails-latest.json').read_text())
    older_dates = pd.bdate_range(end=pd.Timestamp(reference['sessions'][0]) - pd.Timedelta(days=1),
                                periods=494).strftime('%Y-%m-%d').tolist()

    def fixture(tickers):
        result = empty_map(tickers)
        result.update(as_of=reference['as_of'][:10], sessions=older_dates + reference['sessions'])
        for index, ticker in enumerate(tickers):
            recent = reference['trails'].get(ticker, [
                {'date':date, 'X':100.5 + i / 50, 'Y':101 + i / 50}
                for i, date in enumerate(reference['sessions'])])
            result['trails'][ticker] = [
                {'date':date, 'X':100 + 2.4 * math.cos(i / 40 + index),
                 'Y':100 - 1.8 * math.sin(i / 40 + index)}
                for i, date in enumerate(older_dates)] + recent
            if ticker == 'AIPO':  # Short-listing coverage must remain explicit.
                result['trails'][ticker] = result['trails'][ticker][-80:]
        return result

    os.environ['SECTOR_MAP_BASE'] = '/sectors'
    os.environ['SECTOR_MAP_TOKEN'] = ''
    with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as tmp:
        app = create_app(store=Store.for_unittest(Path(tmp) / 'smoke.sqlite3'), fetcher=fixture)

        async def strip_prefix(scope, receive, send):
            # Simulate Traefik: the browser uses /sectors, the app sees stripped
            # paths with no ASGI root_path. Internal health probes still work.
            if scope['type'] == 'http' and (scope['path'] == '/sectors' or
                                          scope['path'].startswith('/sectors/')):
                path = scope['path'][len('/sectors'):] or '/'
                scope = {**scope, 'path':path, 'raw_path':path.encode()}
            await app(scope, receive, send)

        uvicorn.run(strip_prefix, host='127.0.0.1', port=port, log_level='error')


async def smoke():
    import websockets

    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    port, debug_port = available_port(), available_port()
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:
        raise RuntimeError('Chrome/Chromium is needed for the optional browser smoke test')
    with ExitStack() as stack:
        tmp = stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT / 'tests'))
        log = stack.enter_context(open(Path(tmp) / 'process.log', 'w+'))
        processes = []
        try:
            processes.append(subprocess.Popen(
                [sys.executable, __file__, '--serve', str(port)], cwd=ROOT, stdout=log, stderr=log))
            processes.append(subprocess.Popen([
                chrome, '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-background-networking', '--no-first-run', '--no-default-browser-check',
                f'--user-data-dir={tmp}/chrome', f'--remote-debugging-port={debug_port}',
                'about:blank'], cwd=ROOT, stdout=log, stderr=log))

            async def get_json(url):
                # A short timeout also lets process startup retry without hanging.
                with urlopen(url, timeout=1) as response:
                    return json.load(response)

            for _ in range(100):
                try:
                    targets = await get_json(f'http://127.0.0.1:{debug_port}/json/list')
                    await get_json(f'http://127.0.0.1:{port}/api/healthz')
                    break
                except (OSError, ValueError):
                    await asyncio.sleep(.1)
            else:
                log.seek(0)
                raise RuntimeError(log.read())

            async with websockets.connect(targets[0]['webSocketDebuggerUrl'], max_size=8_000_000) as ws:
                seq, errors = 0, []

                async def cdp(method, params=None):
                    nonlocal seq
                    seq += 1
                    identifier = seq
                    await ws.send(json.dumps({'id':identifier, 'method':method, 'params':params or {}}))
                    while True:
                        message = json.loads(await asyncio.wait_for(ws.recv(), 15))
                        if message.get('method') == 'Runtime.exceptionThrown':
                            errors.append(message['params']['exceptionDetails'])
                        if message.get('id') == identifier:
                            if 'error' in message:
                                raise AssertionError(message['error'])
                            return message.get('result', {})

                async def js(source):
                    result = await cdp('Runtime.evaluate', {'expression':source,
                                       'awaitPromise':True, 'returnByValue':True})
                    if 'exceptionDetails' in result:
                        raise AssertionError(result['exceptionDetails'])
                    return result.get('result', {}).get('value')

                async def wait(expression):
                    await js(f"""new Promise((resolve,reject)=>{{
                      const start=Date.now(), timer=setInterval(()=>{{
                        if({expression}){{clearInterval(timer);resolve(true)}}
                        else if(Date.now()-start>8000){{clearInterval(timer);reject(Error('Timed out: '+{json.dumps(expression)}))}}
                      }},50)
                    }})""")

                async def click(selector):
                    await js(f'document.querySelector({json.dumps(selector)}).click()')

                async def fill(selector, value):
                    await js(f'document.querySelector({json.dumps(selector)}).value={json.dumps(value)}')

                await cdp('Runtime.enable')
                await cdp('Page.enable')
                await cdp('Emulation.setDeviceMetricsOverride', {'width':1440, 'height':1200, 'deviceScaleFactor':1, 'mobile':False})
                await cdp('Page.navigate', {'url':f'http://127.0.0.1:{port}/sectors'})
                await wait("document.querySelectorAll('.ticker-label').length===11")
                await wait("document.querySelectorAll('#pattern-rows tr').length===11")
                await click('#generate-summary')
                await wait("document.querySelector('#summary-text').textContent.includes('👉')")
                assert await js("document.querySelector('#summary-text').textContent.match(/👉/g).length") == 1
                # Crowded reference must have non-overlapping label boxes, outside title boxes.
                assert await js("""(()=>{
                  const boxes=[...document.querySelectorAll('.ticker-label')].map(n=>n.getBBox());
                  const titles=[...document.querySelectorAll('.quadrant-title')].map(n=>n.getBBox());
                  const overlap=(a,b)=>a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y;
                  return !boxes.some((a,i)=>boxes.slice(i+1).some(b=>overlap(a,b))||titles.some(b=>overlap(a,b)))
                })()""")
                shot = await cdp('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':True})
                (artifacts / 'smoke-desktop.png').write_bytes(base64.b64decode(shot['data']))

                # Focus via label, keyboard, legend and trail; hover an older point.
                await js("document.querySelector('.ticker-label[data-ticker=XLK]').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
                assert await js("document.querySelector('.trail-layer[data-ticker=XLK]').getAttribute('opacity')") == '1'
                assert await js("document.querySelector('.trail-layer[data-ticker=XLF]').getAttribute('opacity')") == '0.12'
                await js("document.querySelector('.ticker-label[data-ticker=XLK]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
                assert await js("document.querySelector('#clear-focus').disabled")
                await click('.legend-focus[aria-label="Highlight XLF"]')
                assert await js("document.querySelector('#focus-status').textContent.includes('XLF highlighted')")
                await click('#clear-focus')
                await js("document.querySelector('.trail-hit[data-ticker=XLK]').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
                assert await js("document.querySelector('#focus-status').textContent.includes('XLK highlighted')")
                await click('#clear-focus')
                await click('input[aria-label="Show XLK"]')
                assert await js("document.querySelectorAll('.ticker-label').length") == 10
                assert await js("document.querySelectorAll('#pattern-rows tr').length") == 11
                await click('input[aria-label="Show XLK"]')

                for length in [63,126,252,504]:
                    await js(f"document.querySelector('#trail-length').value='{length}'; document.querySelector('#trail-length').dispatchEvent(new Event('change'))")
                    await wait(f"document.querySelector('#patterns-status').textContent.startsWith('{length}-session')")
                    assert await js("document.querySelector('#copy-summary').disabled"), 'Window change leaves a stale summary'
                    assert await js(f"document.querySelector('#pattern-rows small').textContent.startsWith('{length}/{length}')")
                assert await js("document.querySelectorAll('#chart *').length < 350"), 'Long trails create too many DOM elements'
                # Actual screen-coordinate hover at the start of the XLK trail.
                coords = await js("""(()=>{
                  const path=document.querySelector('.trail-hit[data-ticker=XLK]'), p=path.getPointAtLength(0);
                  const screen=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());
                  return {x:screen.x,y:screen.y};
                })()""")
                await cdp('Input.dispatchMouseEvent', {'type':'mouseMoved', **coords})
                await wait("document.querySelector('#tooltip').style.display==='block'")
                assert await js("document.querySelector('#tooltip').textContent.includes('X ')")
                # Test both Markdown copy actions with a controlled clipboard sink.
                await js("Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedText=text}},configurable:true})")
                await click('#copy-patterns')
                assert await js("window.copiedText.includes('504 個交易日') && window.copiedText.includes('唔係回報百分比')")
                await click('#generate-summary')
                await wait("!document.querySelector('#copy-summary').disabled")
                await click('#copy-summary')
                assert await js("window.copiedText.includes('板塊輪動小結')")
                await click('.legend-focus[aria-label="Highlight XLK"]')
                shot = await cdp('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':True})
                (artifacts / 'smoke-long-trail.png').write_bytes(base64.b64decode(shot['data']))
                await click('#clear-focus')

                # Save/load/overwrite/delete through the actual template controls.
                await fill('#template-name', 'Morning')
                await js("document.querySelector('#save-template-form').requestSubmit()")
                await wait("document.querySelector('#template-select').selectedOptions[0]?.textContent==='Morning'")
                await click('#manage')
                await fill('#new-group', 'Free tickers')
                await js("document.querySelector('#new-group-form').requestSubmit()")
                await fill('#new-tickers', 'MAGS, AIPO, DRAM, AAPL')
                await js("document.querySelector('#add-tickers-form').requestSubmit()")
                await click('#use-group')
                await click('#save-groups')
                await wait("!document.querySelector('#editor').open && document.querySelectorAll('.ticker-label').length===4")
                await wait("document.querySelector('#patterns-status').textContent.includes('shorter history')")
                assert await js("[...document.querySelectorAll('#pattern-rows small')].some(n=>n.textContent.includes('80/504'))")
                await click('#generate-summary')
                await wait("document.querySelector('#summary-text').textContent.includes('Free tickers')")
                await click('#load-template')
                await wait("document.querySelectorAll('.ticker-label').length===11")
                await click('#overwrite-template')
                await wait("!document.querySelector('#overwrite-template').disabled")
                await click('#delete-template')
                await wait("document.querySelector('#template-select').options.length===1")
                await click('#toggle')
                assert await js("document.querySelectorAll('.ticker-label').length") == 0
                await click('#toggle')
                assert await js("document.querySelectorAll('.ticker-label').length") == 11
                await cdp('Emulation.setDeviceMetricsOverride', {'width':390, 'height':844, 'deviceScaleFactor':1, 'mobile':True})
                assert await js('document.documentElement.scrollWidth <= window.innerWidth'), 'Page overflows on mobile'
                await click('#generate-summary')
                await wait("document.querySelector('#summary-text').textContent.includes('👉')")
                shot = await cdp('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':True})
                (artifacts / 'smoke-mobile.png').write_bytes(base64.b64decode(shot['data']))
                assert not errors, errors
                print('Browser smoke passed: labels, focus, keyboard, hover, 5 windows, patterns, copy, summary, free tickers, templates, toggles, mobile, /sectors')
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', type=int)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
    else:
        import uvloop
        asyncio.run(smoke(), loop_factory=uvloop.new_event_loop)

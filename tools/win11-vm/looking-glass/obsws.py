#!/usr/bin/env python3
# Minimal obs-websocket v5 client for the host OBS. Usage: obsws.py '[{"requestType":"GetStats"}]'
import sys, json, asyncio, base64, hashlib, os, websockets
cfg = json.load(open(os.path.expanduser('~/.config/obs-studio/plugin_config/obs-websocket/config.json')))
async def main(reqs):
    async with websockets.connect(f"ws://127.0.0.1:{cfg['server_port']}", max_size=None) as ws:
        hello = json.loads(await ws.recv())['d']; ident = {'rpcVersion': 1}
        if 'authentication' in hello:
            a = hello['authentication']
            secret = base64.b64encode(hashlib.sha256((cfg['server_password'] + a['salt']).encode()).digest()).decode()
            ident['authentication'] = base64.b64encode(hashlib.sha256((secret + a['challenge']).encode()).digest()).decode()
        await ws.send(json.dumps({'op': 1, 'd': ident})); await ws.recv()
        for i, r in enumerate(reqs):
            await ws.send(json.dumps({'op': 6, 'd': {'requestType': r['requestType'], 'requestId': str(i), 'requestData': r.get('requestData', {})}}))
            while True:
                m = json.loads(await ws.recv())
                if m['op'] == 7:
                    d = m['d']; print(f"{d['requestType']}: {json.dumps(d['requestStatus'])} {json.dumps(d.get('responseData', {}))}"); break
asyncio.run(main(json.loads(sys.argv[1])))

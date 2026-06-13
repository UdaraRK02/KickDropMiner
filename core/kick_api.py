import time
import asyncio
import random
from curl_cffi import requests, AsyncSession

DEFAULT_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://kick.com/',
    'Origin': 'https://kick.com',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}

_AUTH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'X-Client-Token': 'e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823',
    'Referer': 'https://kick.com/',
    'Origin': 'https://kick.com',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'Sec-Ch-Ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
}


def _authed_session(session_token: str) -> requests.Session:
    s = requests.Session(impersonate='chrome120')
    s.headers.update({**_AUTH_HEADERS, 'Authorization': f'Bearer {session_token}'})
    return s


# ── Public (no auth) ──────────────────────────────────────────────────────────

def get_all_campaigns() -> dict:
    url = 'https://web.kick.com/api/v1/drops/campaigns'
    response = requests.get(url, headers=DEFAULT_HEADERS, impersonate='chrome120')
    return response.json()


def get_random_stream_from_category(category_id: int, limit: int = 10) -> dict:
    url = (
        f'https://web.kick.com/api/v1/livestreams'
        f'?limit={limit}&sort=viewer_count_desc&category_id={category_id}'
    )
    response = requests.get(url, headers=DEFAULT_HEADERS, impersonate='chrome120')
    data = response.json()

    result = {'username': None, 'channel_id': None}
    if data and 'data' in data:
        streams = data['data'].get('livestreams', [])
        if streams:
            max_idx = min(4, len(streams) - 1)
            idx = random.randint(1, max_idx) if max_idx >= 1 else 0
            channel = streams[idx].get('channel', {})
            result['username'] = channel.get('username')
            result['channel_id'] = channel.get('id')
    return result


def get_channel_id(channel_name: str) -> int | None:
    for attempt in range(3):
        s = requests.Session(impersonate='chrome120')
        try:
            s.headers.update(DEFAULT_HEADERS)
            r = s.get(f'https://kick.com/api/v2/channels/{channel_name}', timeout=10)
            if r.status_code == 200:
                return r.json().get('id')
        except Exception:
            pass
        finally:
            s.close()
        time.sleep(2)
    return None


# ── Authenticated ─────────────────────────────────────────────────────────────

def get_drops_progress(session_token: str) -> dict | None:
    for attempt in range(3):
        s = _authed_session(session_token)
        try:
            r = s.get('https://web.kick.com/api/v1/drops/progress', timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        finally:
            s.close()
        if attempt < 2:
            time.sleep(2)
    return None


def claim_drop_reward(reward_id, campaign_id, session_token: str) -> dict | None:
    payload = {'reward_id': reward_id, 'campaign_id': campaign_id}
    for attempt in range(3):
        s = _authed_session(session_token)
        s.headers.update({
            'Content-Type': 'application/json',
            'x-app-platform': 'web',
        })
        try:
            r = s.post('https://web.kick.com/api/v1/drops/claim', json=payload, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        finally:
            s.close()
        if attempt < 2:
            time.sleep(2)
    return None


def get_ws_token(session_token: str) -> str | None:
    """Exchange the session token for a short-lived websocket connection token."""
    for attempt in range(5):
        s = _authed_session(session_token)
        try:
            r = s.get('https://websockets.kick.com/viewer/v1/token', timeout=10)
            if r.status_code == 200:
                token = r.json().get('data', {}).get('token')
                if token:
                    return token
        except Exception:
            pass
        finally:
            s.close()
        if attempt < 4:
            time.sleep(3 + attempt)
    return None


# ── Async ─────────────────────────────────────────────────────────────────────

async def get_stream_info(username: str) -> dict:
    url = f'https://kick.com/api/v2/channels/{username}/videos'
    result = {'is_live': False, 'game_id': None, 'game_name': None, 'live_stream_id': None}

    async with AsyncSession(impersonate='chrome120') as session:
        try:
            response = await session.get(url, headers=DEFAULT_HEADERS)
            data = response.json()
            if data:
                first = data[0]
                result['is_live'] = first.get('is_live', False)
                result['live_stream_id'] = first.get('id')
                cats = first.get('categories', [])
                if cats:
                    result['game_id'] = cats[0].get('id')
                    result['game_name'] = cats[0].get('name')
        except Exception:
            pass
    return result


async def connection_channel(
    channel_id: int,
    username: str,
    category: int,
    token: str,
    on_progress=None,
    on_log=None,
    stop_event: asyncio.Event = None,
) -> bool:
    """
    Watch a stream via websocket.
    Calls on_progress(seconds) every ~60 s and on_log(msg) for status.
    Returns True if stream ended naturally, False if stop_event fired.
    """

    def log(msg: str):
        if on_log:
            on_log(msg)

    last_report = time.time()
    max_retries = 10
    retries = 0

    while retries < max_retries:
        if stop_event and stop_event.is_set():
            return False

        try:
            current_info = await get_stream_info(username)
            if not current_info['is_live']:
                log(f'[{username}] Offline')
                return True

            async with AsyncSession(impersonate='chrome120') as session:
                ws = await session.ws_connect(
                    f'wss://websockets.kick.com/viewer/v1/connect?token={token}',
                    headers=DEFAULT_HEADERS,
                )
                log(f'[{username}] Connected')
                retries = 0
                counter = 0

                while True:
                    if stop_event and stop_event.is_set():
                        return False

                    counter += 1
                    try:
                        if counter % 2 == 0:
                            await ws.send_json({'type': 'ping'})
                        else:
                            await ws.send_json({
                                'type': 'channel_handshake',
                                'data': {'message': {'channelId': channel_id}},
                            })

                        try:
                            await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass

                        now = time.time()
                        if now - last_report >= 60:
                            if current_info.get('live_stream_id'):
                                await ws.send_json({
                                    'type': 'user_event',
                                    'data': {'message': {
                                        'name': 'tracking.user.watch.livestream',
                                        'channel_id': channel_id,
                                        'livestream_id': current_info['live_stream_id'],
                                    }},
                                })
                            if on_progress:
                                on_progress(60)
                            last_report = now

                        if random.randint(1, 3) == 2:
                            info = await get_stream_info(username)
                            if not info['is_live'] or info['game_id'] != category:
                                log(f'[{username}] Stream ended or game changed')
                                elapsed = int(now - last_report)
                                if on_progress and elapsed > 0:
                                    on_progress(elapsed)
                                return True

                        await asyncio.sleep(11 + random.randint(2, 7))

                    except Exception as e:
                        log(f'[{username}] Send error: {e}')
                        break

        except Exception as e:
            retries += 1
            log(f'[{username}] Connection error ({retries}/{max_retries}): {e}')
            if '403' in str(e):
                log('[!] Session token expired — please log in again')
                return True
            if retries < max_retries:
                await asyncio.sleep(random.randint(5, 10))

    return True

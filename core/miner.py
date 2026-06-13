import asyncio
import random
from typing import Callable
from core import kick_api, auth_manager
from core.drop_manager import DropManager, Drop


class Miner:
    """
    Async drop miner.  Must be created and run inside an asyncio event loop
    (typically a dedicated QThread).  stop_event must be created in that same loop.
    log_cb and status_cb are called directly — wire them to Qt signals.
    """

    def __init__(
        self,
        dm: DropManager,
        stop_event: asyncio.Event,
        log_cb: Callable[[str], None] | None = None,
        status_cb: Callable[[str], None] | None = None,
    ):
        self.dm = dm
        self._stop = stop_event
        self._log_cb = log_cb or print
        self._status_cb = status_cb or (lambda _: None)

        self.current_drop: Drop | None = None
        self.current_username: str | None = None

    # ------------------------------------------------------------------

    def _log(self, msg: str):
        self._log_cb(msg)

    def _status(self, msg: str):
        self._status_cb(msg)

    async def _sleep(self, seconds: float):
        end = asyncio.get_event_loop().time() + seconds
        while not self._stop.is_set():
            left = end - asyncio.get_event_loop().time()
            if left <= 0:
                return
            await asyncio.sleep(min(0.5, left))

    # ------------------------------------------------------------------

    async def run(self):
        self._log('[*] Miner started')
        self._status('Running')
        try:
            while not self._stop.is_set():
                await self._claim_ready_drops()

                drop = self.dm.get_next_pending()
                if drop is None:
                    self._log('[*] No pending drops — waiting 60 s')
                    self._status('Idle')
                    await self._sleep(60)
                    continue

                self.current_drop = drop
                if drop.drop_type == 1:
                    await self._mine_type1(drop)
                else:
                    await self._mine_type2(drop)

                self.current_drop = None
                self.current_username = None
                await self._sync_progress()
                await self._claim_ready_drops()

        except asyncio.CancelledError:
            pass
        finally:
            self._log('[*] Miner stopped')
            self._status('Stopped')
            self.current_drop = None
            self.current_username = None

    # ------------------------------------------------------------------

    async def _mine_type1(self, drop: Drop):
        while not self._stop.is_set() and drop.remaining_minutes > 0:
            found = False
            for username in drop.usernames:
                if self._stop.is_set():
                    return
                info = await kick_api.get_stream_info(username)
                if info['is_live'] and info['game_id'] == drop.category_id:
                    found = True
                    await self._watch(username, drop)
                    break
                self._log(f'    [{username}] offline or wrong game')

            if not found:
                self._log(f'[!] No live streamers for "{drop.campaign_name}" — waiting 5 min')
                self._status(f'Waiting: {drop.campaign_name} streamers offline')
                await self._sleep(300)

    async def _mine_type2(self, drop: Drop):
        while not self._stop.is_set() and drop.remaining_minutes > 0:
            stream = await asyncio.to_thread(
                kick_api.get_random_stream_from_category, drop.category_id
            )
            if not stream['username']:
                self._log(f'[!] No streams for category {drop.category_id} — waiting 5 min')
                self._status(f'Waiting: no streams in category {drop.category_id}')
                await self._sleep(300)
                continue
            await self._watch(stream['username'], drop)

    # ------------------------------------------------------------------

    async def _watch(self, username: str, drop: Drop):
        session_token = await asyncio.to_thread(auth_manager.get_session_token)
        if not session_token:
            self._log('[!] Not logged in — use File → Login')
            await self._sleep(30)
            return

        ws_token = await asyncio.to_thread(kick_api.get_ws_token, session_token)
        if not ws_token:
            self._log('[!] Failed to get websocket token — session may be expired')
            await self._sleep(60)
            return

        channel_id = await asyncio.to_thread(kick_api.get_channel_id, username)
        if not channel_id:
            self._log(f'[!] Could not resolve channel ID for {username}')
            return

        self.current_username = username
        self._status(
            f'Watching {username} | {drop.reward_name} | {drop.remaining_minutes:.0f} min left'
        )
        self._log(f'[>] Watching {username} — {drop.remaining_minutes:.0f} min remaining')

        def on_progress(seconds: int):
            self.dm.update_progress(drop.id, seconds)
            self._status(
                f'Watching {username} | {drop.reward_name} | {drop.remaining_minutes:.0f} min left'
            )

        timeout = drop.remaining_minutes * 60 + 120
        try:
            await asyncio.wait_for(
                kick_api.connection_channel(
                    channel_id, username, drop.category_id, ws_token,
                    on_progress=on_progress,
                    on_log=self._log,
                    stop_event=self._stop,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self._log(f'[{username}] Watch timer expired')
        finally:
            self.current_username = None

    # ------------------------------------------------------------------

    async def _claim_ready_drops(self):
        claimable = self.dm.get_claimable()
        if not claimable:
            return
        session_token = await asyncio.to_thread(auth_manager.get_session_token)
        if not session_token:
            return
        for drop in claimable:
            if self._stop.is_set():
                return
            self._log(f'[+] Claiming: {drop.reward_name} ({drop.campaign_name})')
            try:
                result = await asyncio.to_thread(
                    kick_api.claim_drop_reward, drop.id, drop.campaign_id, session_token
                )
                if result and result.get('message') == 'Success':
                    self.dm.mark_claimed(drop.id)
                    self._log(f'[✓] Claimed: {drop.reward_name}')
                else:
                    self._log(f'[!] Claim failed for {drop.reward_name}: {result}')
            except Exception as exc:
                self._log(f'[!] Claim error for {drop.reward_name}: {exc}')
            await asyncio.sleep(2)

    async def _sync_progress(self):
        session_token = await asyncio.to_thread(auth_manager.get_session_token)
        if not session_token:
            return
        try:
            progress = await asyncio.to_thread(kick_api.get_drops_progress, session_token)
            if progress:
                self.dm.sync_progress(progress)
                self._log('[*] Progress synced from API')
        except Exception as exc:
            self._log(f'[!] Progress sync failed: {exc}')

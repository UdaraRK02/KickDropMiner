import json
import os
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Drop:
    id: int
    campaign_id: int
    campaign_name: str
    reward_name: str
    required_minutes: float
    watched_minutes: float
    claimed: bool
    drop_type: int          # 1 = specific streamers, 2 = any in category
    usernames: list
    category_id: int
    priority: int
    enabled: bool = True

    @property
    def remaining_minutes(self) -> float:
        return max(0.0, self.required_minutes - self.watched_minutes)

    @property
    def progress_pct(self) -> float:
        if self.required_minutes <= 0:
            return 100.0
        return min(100.0, (self.watched_minutes / self.required_minutes) * 100.0)

    @property
    def status(self) -> str:
        if self.claimed:
            return 'Claimed'
        if self.remaining_minutes <= 0:
            return 'Ready'
        return 'Pending'


class DropManager:
    def __init__(self, save_path: str = 'drops_state.json'):
        self.save_path = save_path
        self.drops: list[Drop] = []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_api(self, campaigns_data: dict):
        """Parse API response into Drop objects, preserving existing progress/priority."""
        existing = {d.id: d for d in self.drops}
        new_drops: list[Drop] = []
        auto_priority = 0

        if 'data' not in campaigns_data:
            return

        for campaign in campaigns_data['data']:
            if campaign.get('status') == 'expired':
                continue

            campaign_id = campaign.get('id')
            campaign_name = campaign.get('name', 'Unknown Campaign')
            category_id = campaign.get('category', {}).get('id')
            if category_id is None:
                continue

            channels = campaign.get('channels', [])
            rewards = campaign.get('rewards', [])

            if not channels:
                # Type 2: any streamer in the category, one entry per reward
                for reward in rewards:
                    rid = reward.get('id')
                    if rid is None:
                        continue
                    if rid in existing:
                        d = existing[rid]
                        d.campaign_name = campaign_name
                        d.reward_name = reward.get('name', d.reward_name)
                        new_drops.append(d)
                    else:
                        new_drops.append(Drop(
                            id=rid,
                            campaign_id=campaign_id,
                            campaign_name=campaign_name,
                            reward_name=reward.get('name', 'Unknown Reward'),
                            required_minutes=float(reward.get('required_units', 0)),
                            watched_minutes=0.0,
                            claimed=bool(reward.get('claimed', False)),
                            drop_type=2,
                            usernames=[],
                            category_id=category_id,
                            priority=auto_priority,
                        ))
                    auto_priority += 1
            else:
                # Type 1: specific streamers
                usernames = [ch.get('slug') for ch in channels if ch.get('slug')]
                total_minutes = float(sum(r.get('required_units', 0) for r in rewards))
                rid = rewards[0].get('id') if rewards else None
                if rid is None:
                    continue
                if rid in existing:
                    d = existing[rid]
                    d.campaign_name = campaign_name
                    d.usernames = usernames
                    d.required_minutes = total_minutes
                    new_drops.append(d)
                else:
                    new_drops.append(Drop(
                        id=rid,
                        campaign_id=campaign_id,
                        campaign_name=campaign_name,
                        reward_name=rewards[0].get('name', 'Unknown') if rewards else 'Unknown',
                        required_minutes=total_minutes,
                        watched_minutes=0.0,
                        claimed=False,
                        drop_type=1,
                        usernames=usernames,
                        category_id=category_id,
                        priority=auto_priority,
                    ))
                auto_priority += 1

        # Keep user-set priorities for existing drops; sort by them
        self.drops = new_drops
        self._sort()
        self.save()

    def sync_progress(self, progress_data: dict):
        """Update claimed/watch progress from the /drops/progress API response."""
        if not progress_data or 'data' not in progress_data:
            return

        reward_map: dict[int, dict] = {}
        for campaign in progress_data.get('data', []):
            for reward in campaign.get('rewards', []):
                rid = reward.get('id')
                if rid is not None:
                    reward_map[rid] = {
                        'claimed': bool(reward.get('claimed', False)),
                        'progress': float(reward.get('progress', 0)),
                    }

        for drop in self.drops:
            if drop.id in reward_map:
                info = reward_map[drop.id]
                drop.claimed = info['claimed']
                if info['progress'] > 0:
                    drop.watched_minutes = drop.required_minutes * info['progress']

        self.save()

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def update_progress(self, drop_id: int, seconds: int):
        for drop in self.drops:
            if drop.id == drop_id:
                drop.watched_minutes = min(
                    drop.required_minutes,
                    drop.watched_minutes + seconds / 60.0,
                )
                self.save()
                return

    def mark_claimed(self, drop_id: int):
        for drop in self.drops:
            if drop.id == drop_id:
                drop.claimed = True
                drop.watched_minutes = drop.required_minutes
                self.save()
                return

    def toggle_enabled(self, drop_id: int):
        for drop in self.drops:
            if drop.id == drop_id:
                drop.enabled = not drop.enabled
                self.save()
                return

    def move_up(self, drop_id: int):
        idx = self._index_of(drop_id)
        if idx > 0:
            self.drops[idx].priority, self.drops[idx - 1].priority = (
                self.drops[idx - 1].priority,
                self.drops[idx].priority,
            )
            self._sort()
            self.save()

    def move_down(self, drop_id: int):
        idx = self._index_of(drop_id)
        if idx != -1 and idx < len(self.drops) - 1:
            self.drops[idx].priority, self.drops[idx + 1].priority = (
                self.drops[idx + 1].priority,
                self.drops[idx].priority,
            )
            self._sort()
            self.save()

    def set_priority(self, drop_id: int, new_position: int):
        """Move drop to new_position (1-based)."""
        idx = self._index_of(drop_id)
        if idx == -1:
            return
        drop = self.drops.pop(idx)
        insert_at = max(0, min(new_position - 1, len(self.drops)))
        self.drops.insert(insert_at, drop)
        self._renumber()
        self.save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_next_pending(self) -> Optional[Drop]:
        for drop in self.drops:
            if drop.enabled and not drop.claimed and drop.remaining_minutes > 0:
                return drop
        return None

    def get_claimable(self) -> list[Drop]:
        return [d for d in self.drops if not d.claimed and d.remaining_minutes <= 0]

    def _index_of(self, drop_id: int) -> int:
        for i, d in enumerate(self.drops):
            if d.id == drop_id:
                return i
        return -1

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        with open(self.save_path, 'w', encoding='utf-8') as f:
            json.dump([asdict(d) for d in self.drops], f, indent=2)

    def load(self) -> bool:
        if not os.path.exists(self.save_path):
            return False
        try:
            with open(self.save_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.drops = [Drop(**d) for d in data]
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sort(self):
        self.drops.sort(key=lambda d: d.priority)

    def _renumber(self):
        for i, d in enumerate(self.drops):
            d.priority = i

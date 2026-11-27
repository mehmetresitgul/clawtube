"""On-disk transcript cache so re-running clawtube (or fetching a video that
appears in two different playlists) never re-downloads the same captions."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

CACHE_DIRNAME = ".clawtube_cache"
# Bump when the shape or quality of cached transcripts changes, so entries
# written by older versions (e.g. with unparsed VTT tags) are ignored.
CACHE_VERSION = 4


class TranscriptCache:
    def __init__(self, root: Optional[Path] = None):
        self.root = (root or Path.home() / ".cache" / "clawtube") / CACHE_DIRNAME
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, video_id: str, langs: tuple[str, ...]) -> Path:
        digest = hashlib.sha1(f"{video_id}:{','.join(langs)}".encode()).hexdigest()[:16]
        return self.root / f"{video_id}_v{CACHE_VERSION}_{digest}.json"

    def get(self, video_id: str, langs: tuple[str, ...]) -> Optional[Dict[str, Any]]:
        path = self._key(video_id, langs)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def set(self, video_id: str, langs: tuple[str, ...], payload: Dict[str, Any]) -> None:
        payload = dict(payload)
        payload["_cached_at"] = time.time()
        self._key(video_id, langs).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

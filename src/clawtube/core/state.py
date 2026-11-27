"""Resumable run state.

A playlist can have hundreds of videos; a run can die halfway through
(network blip, rate limit, Ctrl-C). We persist per-video status to a
`.clawtube_state.json` file in the output directory so re-running the same
command skips whatever already succeeded instead of starting over.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

STATE_FILENAME = ".clawtube_state.json"


@dataclass
class VideoState:
    status: str  # "pending" | "done" | "failed" | "skipped"
    source: Optional[str] = None  # which fetch method succeeded
    error: Optional[str] = None
    output_path: Optional[str] = None


class RunState:
    def __init__(self, out_dir: Path):
        self.path = out_dir / STATE_FILENAME
        self._lock = threading.Lock()
        self.videos: Dict[str, VideoState] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for vid, data in raw.get("videos", {}).items():
                    self.videos[vid] = VideoState(**data)
            except (json.JSONDecodeError, TypeError):
                # Corrupt state file - start fresh rather than crash the run.
                self.videos = {}

    def save(self) -> None:
        with self._lock:
            payload = {"videos": {vid: asdict(s) for vid, s in self.videos.items()}}
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def is_done(self, video_id: str) -> bool:
        return self.videos.get(video_id, VideoState("pending")).status == "done"

    def mark(self, video_id: str, status: str, **kwargs) -> None:
        with self._lock:
            self.videos[video_id] = VideoState(status=status, **kwargs)
        self.save()

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for s in self.videos.values():
            counts[s.status] = counts.get(s.status, 0) + 1
        return counts

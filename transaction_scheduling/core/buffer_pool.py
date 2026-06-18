import json
import time
from pathlib import Path
from typing import Dict, List


class BufferPool:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.touch(exist_ok=True)

    def submit(self, tx: Dict) -> None:
        tx.setdefault("timestamp", int(time.time() * 1000))
        with self.file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(tx, ensure_ascii=False) + "\n")

    def read_all(self) -> List[Dict]:
        items: List[Dict] = []
        with self.file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items

    def get_window(self, window_size: int, max_wait_ms: int) -> List[Dict]:
        deadline = time.time() + max_wait_ms / 1000.0
        while True:
            items = self.read_all()
            if len(items) >= window_size:
                return items[:window_size]
            if time.time() >= deadline:
                return items
            time.sleep(0.1)

    def truncate(self, keep_from: int) -> None:
        items = self.read_all()
        with self.file_path.open("w", encoding="utf-8") as f:
            for item in items[keep_from:]:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

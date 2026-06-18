import threading
import json
import csv
from pathlib import Path
from typing import List, Dict

class MetricExtractor:
    def __init__(self, output_dir: Path):
        self.lock = threading.Lock()
        self.output_dir = output_dir
        self.all_records: List[Dict] = []
        
        self.fieldnames = [
            'batch_id', 'group_name', 'succ', 'fail',
            'send_rate', 'max_latency', 'min_latency', 'avg_latency', 'throughput'
        ]

    def add_batch_results(self, batch_id: int, records: List[Dict]):
        """
        接收从 Caliper 日志中解析出的多行数据
        """
        with self.lock:
            for record in records:
                record['batch_id'] = batch_id
                self.all_records.append(record)

    def save_to_csv(self, filename: str = "detailed_metrics.csv"):
        file_path = self.output_dir / filename
        with self.lock:
            if not self.all_records:
                return
            
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()
                writer.writerows(self.all_records)
            print(f"[Extractor] Detailed metrics saved to {file_path}")

    def get_all_records(self) -> List[Dict]:
        with self.lock:
            return list(self.all_records)

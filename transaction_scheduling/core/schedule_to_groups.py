import json
from pathlib import Path
from typing import Dict


def emit_groups(schedule_path: str, transactions_path: str, output_dir: str) -> Dict[int, str]:
    schedule = json.loads(Path(schedule_path).read_text(encoding='utf-8'))
    transactions = json.loads(Path(transactions_path).read_text(encoding='utf-8'))
    tx_map = {tx['id']: tx for tx in transactions}
    blocks = schedule.get('blocks', {})
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    group_files: Dict[int, str] = {}
    for k, tx_ids in blocks.items():
        idx = int(k.split('_')[1]) if isinstance(k, str) and '_' in k else int(k)
        group = [tx_map[tx_id] for tx_id in tx_ids if tx_id in tx_map]
        file_path = out / f'group_{idx}.json'
        file_path.write_text(json.dumps(group, indent=2, ensure_ascii=False), encoding='utf-8')
        group_files[idx] = str(file_path)
    return group_files
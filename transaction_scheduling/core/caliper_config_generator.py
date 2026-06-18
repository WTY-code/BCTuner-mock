from pathlib import Path
from typing import Dict, List


def generate_config(group_files: Dict[int, str], tps: int, tx_numbers: Dict[int, int], workspace_rel_module: str, workers: int = 1, rate_control: dict = None, contract_id: str = "smallbank", contract_version: str = "1.0") -> dict:
    rounds = []
    
    # 默认速率控制配置
    if rate_control is None:
        rate_control = {'type': 'fixed-rate', 'opts': {'tps': tps}}
    
    # 如果用户只提供了 type，没有提供 opts，我们尝试补充 tps
    if 'opts' not in rate_control and rate_control.get('type') == 'fixed-rate':
        rate_control['opts'] = {'tps': tps}

    # 原始模式：每个 Group 一个 Round
    for idx in sorted(group_files.keys()):
        tx_file = group_files[idx]
        tx_number = tx_numbers.get(idx, 0)
        rounds.append({
            'label': f'group_{idx}',
            'txNumber': tx_number,
            'rateControl': rate_control,
            'workload': {
                'module': workspace_rel_module,
                'arguments': {
                    'contractId': contract_id,
                    'contractVersion': contract_version,
                    'txFilePath': tx_file
                }
            }
        })
            
    return {'test': {'workers': {'number': workers}, 'rounds': rounds}}


def write_yaml(config: dict, output_path: str) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    test = config.get('test', {})
    workers = test.get('workers', {}).get('number', 1)
    rounds = test.get('rounds', [])
    lines: List[str] = []
    lines.append('test:')
    lines.append('  workers:')
    lines.append(f'    number: {workers}')
    if rounds:
        lines.append('  rounds:')
        for r in rounds:
            lines.append('    - label: ' + str(r.get('label', 'group')))
            lines.append('      txNumber: ' + str(r.get('txNumber', 0)))
            lines.append('      rateControl:')
            rc = r.get('rateControl', {})
            lines.append('        type: ' + str(rc.get('type', 'fixed-rate')))
            opts = rc.get('opts', {})
            if opts:
                lines.append('        opts:')
                for k, v in opts.items():
                    lines.append(f'          {k}: {v}')
            wl = r.get('workload', {})
            lines.append('      workload:')
            lines.append('        module: ' + str(wl.get('module', '')))
            args = wl.get('arguments', {})
            lines.append('        arguments:')
            lines.append('          contractId: ' + str(args.get('contractId', 'smallbank')))
            lines.append("          contractVersion: '" + str(args.get('contractVersion', '1.0')) + "'")
            lines.append('          txFilePath: ' + str(args.get('txFilePath', '')))
    else:
        lines.append('  rounds: []')
    out.write_text('\n'.join(lines), encoding='utf-8')

#!/usr/bin/env python3
"""
基于容量限制图着色的交易调度器
在 DSATUR 算法基础上加入区块容量限制
"""

import json
import argparse
import heapq
from typing import Dict, List, Set, Tuple
from collections import defaultdict
import os
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class CapacitatedGraphColoringScheduler:
    """容量限制图着色调度器 - 带区块大小限制的 DSATUR 算法"""
    
    def __init__(self, conflict_graph: Dict[str, List[str]], block_size: int):
        self.conflict_graph = conflict_graph
        self.block_size = block_size
        self.colors = {}  # tx_id -> color (block number)
        self.saturation = {}  # 节点饱和度
        self.degrees = {}  # 节点度数
        self.color_usage = defaultdict(int)  # 每个颜色已使用的容量
        
        # 计算每个节点的度数
        self.degrees = {node: len(neighbors) for node, neighbors in self.conflict_graph.items()}
    
    def capacitated_dsatur_coloring(self) -> Dict[str, int]:
        """
        带容量限制的 DSATUR 图着色算法
        
        算法改进:
        - 在选择颜色时检查区块容量限制
        - 如果所有可用颜色都已满，创建新颜色
        - 优先选择饱和度高的颜色，但受容量限制
        
        Returns:
            交易ID到颜色(区块号)的映射
        """
        if not self.conflict_graph:
            print("警告: 冲突图为空")
            return {}
        
        # 初始化数据结构
        self.colors = {}
        self.saturation = {node: 0 for node in self.conflict_graph}
        self.color_usage = defaultdict(int)
        
        # 使用优先队列（最小堆）存储 (-饱和度, -度数, 节点)
        heap = []
        processed = set()
        
        # 初始化堆
        for node in self.conflict_graph:
            heapq.heappush(heap, (-self.saturation[node], -self.degrees[node], node))
        
        # 着色过程
        while heap:
            # 弹出优先级最高的节点
            neg_sat, neg_deg, node = heapq.heappop(heap)
            
            if node in processed:
                continue
            
            processed.add(node)
            
            # 收集邻居已使用的颜色
            used_colors = set()
            for neighbor in self.conflict_graph[node]:
                if neighbor in self.colors:
                    used_colors.add(self.colors[neighbor])
            
            # 找到第一个可用且有容量的颜色
            color = self._find_available_color(used_colors)
            
            # 为节点分配颜色
            self.colors[node] = color
            self.color_usage[color] += 1
            
            # 更新未处理邻居的饱和度
            for neighbor in self.conflict_graph[node]:
                if neighbor not in processed:
                    neighbor_colors = {self.colors[n] for n in self.conflict_graph[neighbor] 
                                     if n in self.colors}
                    new_saturation = len(neighbor_colors)
                    
                    if new_saturation > self.saturation[neighbor]:
                        self.saturation[neighbor] = new_saturation
                        heapq.heappush(heap, 
                                      (-self.saturation[neighbor], 
                                       -self.degrees[neighbor], 
                                       neighbor))
        
        return self.colors

    def _find_available_color(self, used_colors: Set[int]) -> int:
        """
        找到第一个可用且有容量的颜色
        
        Args:
            used_colors: 邻居已使用的颜色集合
            
        Returns:
            可用的颜色编号
        """
        # 首先尝试现有颜色（按编号顺序）
        for color in sorted(self.color_usage.keys()):
            if color not in used_colors and self.color_usage[color] < self.block_size:
                return color
        
        # 如果现有颜色都不可用或已满，创建新颜色
        new_color = max(self.color_usage.keys(), default=-1) + 1
        return new_color
    
    def get_blocks(self) -> Dict[int, List[str]]:
        """将着色结果转换为区块分配"""
        if not self.colors:
            raise ValueError("请先运行着色算法")
        
        blocks = defaultdict(list)
        for tx_id, block_num in self.colors.items():
            blocks[block_num].append(tx_id)
        
        return dict(blocks)
    
    def get_chromatic_number(self) -> int:
        """获取色数（所需区块数）"""
        if not self.colors:
            raise ValueError("请先运行着色算法")
        return max(self.colors.values()) + 1
    
    def verify_coloring(self) -> Tuple[bool, List[str]]:
        """
        验证着色方案
        
        Returns:
            (是否有效, 错误信息列表)
        """
        if not self.colors:
            return False, ["未运行着色算法"]
        
        errors = []
        
        # 检查冲突
        for node, neighbors in self.conflict_graph.items():
            node_color = self.colors[node]
            for neighbor in neighbors:
                if self.colors[neighbor] == node_color:
                    errors.append(f"冲突: {node} 和 {neighbor} 在同一区块 {node_color}")
        
        # 检查容量限制
        block_sizes = defaultdict(int)
        for tx_id, block_num in self.colors.items():
            block_sizes[block_num] += 1
        
        for block_num, size in block_sizes.items():
            if size > self.block_size:
                errors.append(f"区块 {block_num} 超出容量限制: {size} > {self.block_size}")
        
        return len(errors) == 0, errors
    
    


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='基于容量限制 DSATUR 算法的交易调度器'
    )
    parser.add_argument(
        '--conflict-graph',
        default=os.path.join(BASE_DIR, 'artifacts/world_state/output/conflict_graph.json'),
        help='冲突图文件路径 (默认: /root/workspace/fangtaogu/PerfTuner/artifacts/world_state/output/conflict_graph.json)'
    )
    parser.add_argument(
        '--output',
        default=os.path.join(BASE_DIR, 'artifacts/world_state/output/schedule.json'),
        help='调度结果输出文件 (默认: /root/workspace/fangtaogu/PerfTuner/artifacts/world_state/output/schedule.json)'   
    )
    parser.add_argument(
        '--blocksize',
        type=int,
        required=True,
        help='每个区块的最大交易数量'
    )
    # 仅执行容量限制 DSATUR，无需算法或统计开关
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("  基于容量限制 DSATUR 算法的交易调度器")
    print("=" * 70)
    print(f"\n配置:")
    print(f"  冲突图: {args.conflict_graph}")
    print(f"  区块大小: {args.blocksize}")
    print(f"  算法: DSATUR (容量限制)")
    
    # 加载冲突图
    try:
        with open(args.conflict_graph, 'r') as f:
            conflict_graph = json.load(f)
        print(f"✓ 加载了 {len(conflict_graph)} 个节点的冲突图")
    except Exception as e:
        print(f"✗ 加载冲突图失败: {e}")
        return
    
    # 创建调度器
    scheduler = CapacitatedGraphColoringScheduler(conflict_graph, args.blocksize)
    
    # 运行着色算法
    print(f"\n运行容量限制 DSATUR 着色算法...")
    scheduler.capacitated_dsatur_coloring()
    
    # 验证着色
    is_valid, errors = scheduler.verify_coloring()
    if is_valid:
        print("✓ 着色方案验证通过")
    else:
        print("✗ 着色方案验证失败:")
        for error in errors[:5]:  # 只显示前5个错误
            print(f"  - {error}")
        if len(errors) > 5:
            print(f"  ... 还有 {len(errors) - 5} 个错误")
        return
    
    # 获取结果
    blocks = scheduler.get_blocks()
    # 计算基础统计信息
    block_sizes_map = {block_num: len(txs) for block_num, txs in blocks.items()}
    sizes = list(block_sizes_map.values())
    total_blocks = len(blocks)
    avg_block_size = sum(sizes) / total_blocks if total_blocks > 0 else 0
    max_block_size = max(sizes) if sizes else 0
    min_block_size = min(sizes) if sizes else 0
    
    print(f"\n调度结果:")
    print(f"  所需区块数: {total_blocks}")
    print(f"  平均每区块交易数: {avg_block_size:.2f}")
    print(f"  最大区块大小: {max_block_size}")
    print(f"  最小区块大小: {min_block_size}")
    
    # 打印详细调度（如果需要）
    # 无平衡调度详情输出
    
    # 保存调度结果
    schedule = {
        'metadata': {
            'total_transactions': len(conflict_graph),
            'block_size': args.blocksize,
            'algorithm': 'capacitated_dsatur',
            'total_blocks': total_blocks,
            'avg_block_size': avg_block_size,
            'max_block_size': max_block_size,
            'min_block_size': min_block_size,
            'block_sizes': block_sizes_map
        },
        'blocks': {f"block_{i}": txs for i, txs in blocks.items()}
        # 'transaction_to_block': scheduler.colors
    }
    
    with open(args.output, 'w') as f:
        json.dump(schedule, f, indent=2)
    print(f"\n✓ 调度结果已保存到: {args.output}")
    
    # 精简输出，无额外性能/平衡性分析


if __name__ == '__main__':
    main()
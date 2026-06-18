# Smallbank 世界状态查询功能

本功能允许您查询Smallbank合约中的所有账户数据并将其保存为JSON文件。

## 功能说明

- **查询函数**: 使用Smallbank合约中的`query_all_accounts`函数
- **返回数据**: 包含所有账户的CustomId、CustomName、SavingsBalance和CheckingBalance
- **输出格式**: JSON文件，默认名称为`world_state.json`

## 使用方法

### 1. 仅查询世界状态

使用专门的配置文件来只执行世界状态查询：

```bash
npx caliper launch manager --caliper-workspace ./ --caliper-benchconfig benchmarks/scenario/smallbank/config-query-only.yaml --caliper-networkconfig networks/fabric/test-network.yaml
```

### 2. 完整基准测试（包含世界状态查询）

使用包含世界状态查询的完整配置：

```bash
npx caliper launch manager --caliper-workspace ./ --caliper-benchconfig benchmarks/scenario/smallbank/config-worldstate.yaml --caliper-networkconfig networks/fabric/test-network.yaml
```

## 配置选项

在配置文件中，您可以自定义以下参数：

```yaml
workload:
  module: benchmarks/scenario/smallbank/queryWorldState.js
  arguments:
    accountsGenerated: 100
    txnPerBatch: 1
    contractId: smallbank
    outputPath: ./world_state.json  # 自定义输出文件路径
```

## 输出示例

生成的JSON文件将包含如下格式的数据：

```json
[
  {
    "CustomId": "1000",
    "CustomName": "ABCDEF GHIJKL",
    "SavingsBalance": 1000000,
    "CheckingBalance": 1000000
  },
  {
    "CustomId": "2000", 
    "CustomName": "MNOPQR STUVWX",
    "SavingsBalance": 950000,
    "CheckingBalance": 1050000
  }
]
```

## 注意事项

1. 确保Smallbank合约已正确部署并包含`query_all_accounts`函数
2. 查询操作是只读的，不会修改区块链状态
3. 输出文件将保存在指定路径，如果文件已存在将被覆盖
4. 建议在有数据的情况下运行查询，否则可能返回空数组

## 文件说明

- `queryWorldState.js`: 主要的工作负载模块
- `config-query-only.yaml`: 仅查状询世界状态的配置文件
- `config-worldstate.yaml`: 包含完整态查询的配置文件
基准测试和世界


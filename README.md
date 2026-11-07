# Multi-Model Eval Harness

一个轻量级多模型评测框架——定义任务、并发跑模型、自动判题、输出结果矩阵。

## 为什么需要它

评测模型不应该是「手工问几个问题然后凭感觉打分」。这个 harness 把评测变成可复现的工程流程：

- **一条命令跑完全部模型**，不用逐个切聊天窗口
- **判题规则写死在任务里**，同一把尺子量所有模型
- **结果落盘可追溯**，今天跑的分数和下周跑的直接对比

## 快速开始

```bash
# 用内置 mock 模型跑示例任务
python3 harness.py --tasks sample_tasks.jsonl --models fake-a,fake-b
```

输出：
```
SCORES | fake-a:3/3(100%) | fake-b:0/3(0%)
Results written to results/20261107T061522Z
```

## 任务格式

`tasks.jsonl`，每行一个任务：

```jsonl
{"id": "t-exact", "prompt": "6*7等于多少？只输出数字。", "expected": "42", "check_type": "exact"}
{"id": "t-contains", "prompt": "描述任务结果。", "expected": "成功", "check_type": "contains"}
{"id": "t-numeric", "prompt": "当前温度多少度？", "expected": {"value": 99.5, "tolerance": 0.2}, "check_type": "numeric_tolerance"}
{"id": "t-custom", "prompt": "判断合法性。", "expected": "my_checker", "check_type": "custom"}
```

### 判题类型

| 类型 | 说明 | expected 格式 |
|---|---|---|
| `exact` | 精确匹配（去首尾空格） | 字符串 |
| `contains` | 包含子串 | 字符串 |
| `numeric_tolerance` | 数值容差 | `{"value": 100, "tolerance": 0.5}` 或 `[100, 0.5]` |
| `custom` | 自定义判题函数 | 注册名（字符串） |

### 自定义判题

```python
from harness import register_custom_checker

def my_checker(expected, actual):
    return len(actual) > 10

register_custom_checker("my_checker", my_checker)
```

## 对接真实模型

替换 `call_model` 函数即可——骨架已预留接口：

```python
def call_model(model: str, prompt: str, **kwargs) -> str:
    # 对接你的 API / SDK / 本地模型
    # 返回模型原始文本输出
    ...
```

支持任何能接受 prompt 返回文本的模型（OpenAI / Claude / DeepSeek / 本地模型 / vLLM / llama.cpp）。

## 命令行

```
python3 harness.py --tasks tasks.jsonl --models m1,m2,m3 [--output results/] [--task-timeout 300]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--tasks` | 是 | 任务文件路径（JSONL） |
| `--models` | 是 | 逗号分隔的模型名列表 |
| `--output` | 否 | 输出目录（默认 `results/`） |
| `--task-timeout` | 否 | 单任务超时秒数（默认 300） |
| `--run-id` | 否 | 运行 ID（默认 UTC 时间戳） |

## 输出结构

```
results/<run_id>/
├── summary.json          # 总分/通过率/耗时汇总
├── <model_a>/
│   └── detail.jsonl      # 逐任务结果
└── <model_b>/
    └── detail.jsonl
```

`summary.json` 示例：
```json
{
  "run_id": "20261107T061522Z",
  "models": {
    "fake-a": {"score": 3, "total": 3, "pass_rate": 1.0, "total_latency_s": 0.0},
    "fake-b": {"score": 0, "total": 3, "pass_rate": 0.0, "total_latency_s": 0.0}
  }
}
```

## 设计原则

- **单文件**：一个 `harness.py` 搞定全部——没有复杂的包依赖
- **并发**：同一任务的所有模型同时调用，不等排队
- **超时兜底**：单任务超时标记 TIMEOUT 不阻塞整体
- **零外部依赖**：只用 Python 标准库（asyncio + json + argparse）
- **可扩展**：判题规则和模型适配层都通过函数注入，不改核心逻辑

## 适用场景

- 模型选型：同一批任务跑 5 个候选模型，看谁分高
- 回归测试：模型升级后重跑任务集，确认没有退步
- Benchmark 搭建：作为自定义 benchmark 的执行引擎
- 教学演示：展示不同模型在结构化任务上的能力差异

## License

MIT
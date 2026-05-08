# 论文采集器 · Paper Crawler

三源学术论文批量采集工具，覆盖 **OpenAlex**（2.5 亿篇） + **Semantic Scholar**（2 亿篇） + **arXiv**（240 万篇）。

特点：限速友好、断点续传、24h 持续运行不封 IP。

## 快速开始

```bash
# 安装依赖
pip install requests

# 运行采集器
python crawl_slow.py
```

输出目录：`./essay_bank/{openalex,semantic_scholar,arxiv}/`

## 设计原理

### 三源轮询

| 数据源 | 规模 | 请求间隔 | 批次大小 | API 类型 |
|--------|------|----------|----------|----------|
| OpenAlex | 2.5 亿篇 | 2s | 5 词条 | REST（无 key） |
| Semantic Scholar | 2 亿篇 | 30s | 2 词条 | REST（公共） |
| arXiv | 240 万篇 | 3s | 3 词条 | Atom XML |

每批完成后休息 10 分钟，避免触发 429 限流。

### 断点续传

进度自动保存到 `_collector_progress.json`，中断后重新运行从断点继续，已完成词条不重复抓取。

### 去重逻辑

同一数据源内按 paper ID 去重；跨源不硬去重（arXiv 预印本与发表版可能各有价值）。

## 自定义搜索词

编辑 `crawl_slow.py` 中的三组词条列表：

- `OA_TERMS` — OpenAlex 搜索词（英语短语，建议 3-5 词）
- `S2_TERMS` — Semantic Scholar 搜索词
- `ARXIV_TERMS` — arXiv 搜索词，格式：`("english keywords", "中文标签")`

```python
OA_TERMS = [
    "your keyword phrase here",
    "another research topic",
]
```

## 命令行

```bash
python crawl_slow.py                 # 全量运行
python crawl_slow.py --skip-oa       # 跳过 OpenAlex
python crawl_slow.py --skip-s2       # 跳过 Semantic Scholar
python crawl_slow.py --skip-arxiv    # 跳过 arXiv
```

## 限速参数调优

在 `crawl_slow.py` 顶部可调整：

```python
BATCH_SLEEP = 600       # 批间休息（秒），默认 10 分钟
OA_REQ_INTERVAL = 2.0   # OpenAlex 请求间隔
S2_REQ_INTERVAL = 30.0  # S2 请求间隔（公共 API 极其严格，不要低于 30s）
ARXIV_REQ_INTERVAL = 3.0
OA_BATCH_SIZE = 5       # 每批处理的搜索词数量
S2_BATCH_SIZE = 2
ARXIV_BATCH_SIZE = 3
```

## 输出格式

每篇论文保存为一个 `.txt` 文件：

```
2024 - Title of the Paper [arXiv_ID].txt
2024 - Title of the Paper [Journal | Field] [OpenAlex_ID].txt
```

文件内容包含：标题、作者、摘要、期刊/会议、引用数、DOI/链接。

## Windows 计划任务（可选）

创建每 3 小时自动运行的定时任务：

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "crawl_slow.py" -WorkingDirectory "D:\Projects\lw-paper-crawler"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 7)
Register-ScheduledTask -TaskName "Paper-Crawler" -Action $action -Trigger $trigger
```

## 文件结构

```
lw-paper-crawler/
├── crawl_slow.py          # 主采集器
├── knowledge_source.py    # 基础框架类
├── requirements.txt       # Python 依赖
├── essay_bank/            # 论文输出（自动创建，已 gitignore）
│   ├── arxiv/
│   ├── openalex/
│   └── semantic_scholar/
├── _collector_progress.json  # 进度文件（自动创建，已 gitignore）
└── _logs/                    # 运行日志（自动创建）
```

## License

MIT

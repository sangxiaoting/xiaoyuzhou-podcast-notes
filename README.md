# xiaoyuzhou-podcast-notes

自动化小宇宙播客笔记生成工具 —— 从 RSS 抓取到结构化 Markdown，全流程本地处理。

## 功能特性

- **RSS 自动抓取**：订阅小宇宙播客，自动检测新节目
- **音频下载**：自动下载播客音频文件
- **本地转录**：基于 [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) 在 Apple Silicon 上本地运行，无需云端 API
- **AI 结构化提炼**：通过 Claude 将转录文本提炼为结构化 Markdown 笔记
- **社交文案生成**：自动生成适合公众号/小红书发布的内容摘要
- **增量处理**：自动跳过已处理的节目，支持断点续跑

## 系统要求

- **macOS** + **Apple Silicon**（M1/M2/M3/M4）
- **Python 3.9+**
- **Claude Code**（用于 LLM 提炼步骤）

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/<your-username>/xiaoyuzhou-podcast-notes.git
cd xiaoyuzhou-podcast-notes

# 2. 运行安装脚本
chmod +x setup.sh
./setup.sh

# 3. 编辑配置文件，添加你关注的播客
vim config.yaml
```

`setup.sh` 会自动：
- 安装 Python 依赖（mlx-whisper, feedparser, requests, pyyaml）
- 从 `config.yaml.example` 创建 `config.yaml`（如不存在）
- 创建输出目录 `~/Desktop/podcast-notes`
- 初始化处理记录 `processed.json`

## 使用方式

在 Claude Code 中使用以下命令：

### 运行完整 Pipeline

```
/podcast-pipeline
```

自动执行：抓取 RSS → 下载音频 → 转录 → AI 提炼 → 输出 Markdown

### 添加新播客

```
/podcast-pipeline add https://www.xiaoyuzhoufm.com/podcast/xxx
```

支持直接粘贴小宇宙播客页面 URL，自动解析 RSS 地址。

### 查看已订阅播客

```
/podcast-pipeline list
```

### 处理本地音频文件

```
/podcast-pipeline process /path/to/audio.mp3
```

## 配置说明

编辑 `config.yaml`：

```yaml
podcasts:
  - name: "播客名称"
    rss: "https://api.xiaoyuzhoufm.com/v1/podcast/rss/<podcast-id>"

filter:
  min_duration_minutes: 10    # 跳过短节目
  max_episodes_per_run: 5     # 每次最多处理几期

output_dir: "~/Desktop/podcast-notes"  # 输出目录
whisper_model: "large-v3-turbo"        # Whisper 模型
```

**如何获取播客 RSS 地址？**

在小宇宙 App 或网页打开播客主页，URL 格式为：
```
https://www.xiaoyuzhoufm.com/podcast/<podcast-id>
```
使用 `/podcast-pipeline add <URL>` 即可自动添加。

## Pipeline 流程

```
┌─────────────┐    ┌─────────────┐    ┌─────────────────┐
│  RSS 抓取   │───>│  音频下载   │───>│  mlx-whisper    │
│  (feedparser)│    │  (requests) │    │  本地转录       │
└─────────────┘    └─────────────┘    └────────┬────────┘
                                               │
                                               v
┌─────────────┐    ┌─────────────┐    ┌─────────────────┐
│  Markdown   │<───│  AI 提炼    │<───│  转录文本       │
│  笔记输出   │    │  (Claude)   │    │  (.transcript)  │
└─────────────┘    └─────────────┘    └─────────────────┘
```

## 输出示例

每期播客生成一个 Markdown 文件，包含：

- **元信息**：播客名、标题、嘉宾、日期、时长
- **核心摘要**：2500-3000 字结构化叙事
- **关键要点**：5-8 个核心观点
- **金句摘录**：3-5 句原话引用
- **社交文案**：800-850 字，可直接用于公众号/小红书

## 技术栈

- [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) — Apple Silicon 优化的语音转录
- [feedparser](https://github.com/kurtmckee/feedparser) — RSS/Atom 解析
- [Claude Code](https://claude.ai/claude-code) — AI 内容提炼
- [小宇宙 FM](https://www.xiaoyuzhoufm.com/) — 播客平台

## License

[MIT](LICENSE)

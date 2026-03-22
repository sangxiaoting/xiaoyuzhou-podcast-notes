---
name: podcast-pipeline
description: 自动化播客处理 pipeline：抓取小宇宙 RSS → 下载音频 → mlx-whisper 本地转录 → LLM 提炼结构化 Markdown
version: 1.0.0
author: user
tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - WebFetch
---

# Podcast Pipeline Skill

自动化处理关注的小宇宙播客：抓取 RSS → 下载音频 → mlx-whisper 本地转录 → LLM 提炼结构化内容 → 输出 Markdown 文件。

## 使用方式

- `/podcast-pipeline` — 运行完整 pipeline（抓取最新 → 转录 → 提炼）
- `/podcast-pipeline add <小宇宙播客URL>` — 添加新播客源
- `/podcast-pipeline list` — 查看已配置的播客
- `/podcast-pipeline process <音频文件路径>` — 处理单个本地音频文件
- `/podcast-pipeline refine <转录文本路径>` — 对已有转录文本进行 LLM 提炼

## 执行指令

当用户调用此 skill 时，根据参数执行对应操作：

### 无参数或 `run`：运行完整 pipeline

```bash
python3 ~/.claude/skills/podcast-pipeline/podcast_pipeline.py run
```

脚本会输出每一期的转录文本路径。对于每个转录文本文件，读取其内容，然后按照下方「LLM 提炼模板」生成结构化 Markdown，保存到输出目录。

### `add <URL>`：添加播客源

```bash
python3 ~/.claude/skills/podcast-pipeline/podcast_pipeline.py add <URL>
```

### `list`：查看已配置播客

```bash
python3 ~/.claude/skills/podcast-pipeline/podcast_pipeline.py list
```

### `process <文件路径>`：处理单个本地音频

```bash
python3 ~/.claude/skills/podcast-pipeline/podcast_pipeline.py process <文件路径>
```

脚本会输出转录文本路径，读取后按模板提炼。

### `refine <转录文本路径>`：对已有转录文本进行 LLM 提炼

```bash
python3 ~/.claude/skills/podcast-pipeline/podcast_pipeline.py refine <转录文本路径>
```

需要在 config.yaml 中配置 `llm` 块。

## LLM 提炼模板

读取转录文本后，按以下结构生成 Markdown：

```markdown
# [播客名] | [期数/标题]

## 元信息
- **播客**: [播客名称]
- **标题**: [本期标题]
- **嘉宾**: [嘉宾信息，从转录内容中提取]
- **发布日期**: [YYYY-MM-DD]
- **时长**: [XX分钟]

## 核心摘要

（2500-3000 字中文，结构化叙事。要求：
1. 按主题/话题分段，每段有小标题
2. 包含具体案例、数据、论据
3. 保持叙事连贯性，不是简单罗列
4. 用自己的语言重新组织，但保留关键术语和表述）

## 关键要点

- （5-8 个 bullet points，每个 1-2 句话概括核心观点）

## 金句摘录

> "原话引用1" —— 说话人

> "原话引用2" —— 说话人

> "原话引用3" —— 说话人

（3-5 句值得记录的原话，保持原文表述）

## 社交文案

（800-850 字，格式要求：
- 开头：「来源：[播客名]，[嘉宾名] 聊了聊 [核心话题]」
- 正文：提炼最有价值的 3-4 个观点，每个观点配具体细节
- 语气：专业但不枯燥，有信息密度
- 适合微信公众号/小红书等平台发布）
```

## 注意事项

1. 转录文本可能很长（数万字），需要完整阅读后提炼
2. 嘉宾信息需要从转录内容中推断（开场白、自我介绍等）
3. 金句需要是原文，不是改写
4. 社交文案需要独立可读，不依赖完整笔记

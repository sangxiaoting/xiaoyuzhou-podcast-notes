#!/usr/bin/env python3
"""Podcast Pipeline: RSS 抓取 → 音频下载 → mlx-whisper 转录 → LLM 提炼结构化 Markdown"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import feedparser
import requests
import yaml

# 路径常量
SKILL_DIR = Path(__file__).parent
CONFIG_PATH = SKILL_DIR / "config.yaml"
PROCESSED_PATH = SKILL_DIR / "processed.json"

# LLM 提炼 Prompt 模板
REFINE_PROMPT = """你是一位专业的播客内容编辑。请根据以下播客转录文本，生成结构化的 Markdown 笔记。

## 播客信息
- 播客名称: {podcast_name}
- 标题: {title}
- 发布日期: {date}
- 时长: {duration_minutes} 分钟

## 转录文本
{transcript}

## 输出要求

请按以下结构输出 Markdown：

# {podcast_name} | {title}

## 元信息
- **播客**: {podcast_name}
- **标题**: {title}
- **嘉宾**: [从转录内容中提取嘉宾信息]
- **发布日期**: {date}
- **时长**: {duration_minutes}分钟

## 核心摘要

（2500-3000 字中文，结构化叙事。要求：
1. 按主题/话题分段，每段有小标题
2. 包含具体案例、数据、论据
3. 保持叙事连贯性，不是简单罗列
4. 用自己的语言重新组织，但保留关键术语和表述）

## 关键要点

- （5-8 个 bullet points，每个 1-2 句话概括核心观点）

## 金句摘录

> "原话引用" —— 说话人

（3-5 句值得记录的原话，保持原文表述）

## 社交文案

（800-850 字，格式要求：
- 开头：「来源：{podcast_name}，[嘉宾名] 聊了聊 [核心话题]」
- 正文：提炼最有价值的 3-4 个观点，每个观点配具体细节
- 语气：专业但不枯燥，有信息密度
- 适合微信公众号/小红书等平台发布）
"""


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def load_processed():
    if not PROCESSED_PATH.exists():
        return {"processed_guids": []}
    with open(PROCESSED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_processed(data):
    with open(PROCESSED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_duration(entry):
    """从 RSS entry 中解析时长（秒）"""
    # itunes:duration 格式: HH:MM:SS 或 MM:SS 或纯秒数
    duration = entry.get("itunes_duration", "")
    if not duration:
        return 0
    if ":" in str(duration):
        parts = str(duration).split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    try:
        return int(duration)
    except (ValueError, TypeError):
        return 0


def get_audio_url(entry):
    """从 RSS entry 中提取音频 URL"""
    for link in entry.get("links", []):
        if link.get("type", "").startswith("audio/") or link.get("href", "").endswith(".mp3"):
            return link["href"]
    # 尝试 enclosures
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("audio/") or enc.get("href", "").endswith(".mp3"):
            return enc["href"]
    return None


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def download_audio(url, dest_path):
    """下载音频文件"""
    print(f"  下载音频: {url}")
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = downloaded * 100 // total
                print(f"\r  进度: {pct}%", end="", flush=True)
    print()
    return dest_path


def transcribe_audio(audio_path, model_name="large-v3-turbo"):
    """使用 mlx-whisper 转录音频"""
    import mlx_whisper

    print(f"  转录中（模型: {model_name}）...")
    start = datetime.now()

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=f"mlx-community/whisper-{model_name}",
        language="zh",
        verbose=False,
    )

    elapsed = (datetime.now() - start).total_seconds()
    print(f"  转录完成，耗时 {elapsed:.0f} 秒")

    return result


def format_transcript(result):
    """将转录结果格式化为带时间戳的文本"""
    lines = []
    for seg in result.get("segments", []):
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()
        h1, m1, s1 = int(start // 3600), int(start % 3600 // 60), int(start % 60)
        h2, m2, s2 = int(end // 3600), int(end % 3600 // 60), int(end % 60)
        ts = f"[{h1:02d}:{m1:02d}:{s1:02d} -> {h2:02d}:{m2:02d}:{s2:02d}]"
        lines.append(f"{ts} {text}")
    return "\n".join(lines)


# ─── LLM 提炼功能 ────────────────────────────────────────────────


def get_llm_config(config):
    """读取 LLM 配置，环境变量优先于 config 中的 api_key"""
    llm = config.get("llm")
    if not llm:
        return None

    provider = llm.get("provider", "").lower()
    if not provider:
        return None

    llm_config = {
        "provider": provider,
        "model": llm.get("model", ""),
        "api_key": llm.get("api_key", ""),
        "base_url": llm.get("base_url", ""),
    }

    # 环境变量优先级高于 config
    env_key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env_var = env_key_map.get(provider)
    if env_var and os.environ.get(env_var):
        llm_config["api_key"] = os.environ[env_var]

    if not llm_config["api_key"]:
        print(f"  警告: 未找到 {provider} 的 API key（检查 config.yaml 或环境变量 {env_var}）")
        return None

    # 设置默认模型和 base_url
    if provider == "anthropic":
        if not llm_config["model"]:
            llm_config["model"] = "claude-sonnet-4-20250514"
    elif provider == "deepseek":
        if not llm_config["model"]:
            llm_config["model"] = "deepseek-chat"
        if not llm_config["base_url"]:
            llm_config["base_url"] = "https://api.deepseek.com"
    elif provider == "openai":
        if not llm_config["model"]:
            llm_config["model"] = "gpt-4o"

    return llm_config


def refine_with_anthropic(transcript, metadata, llm_config):
    """使用 Anthropic API 提炼转录文本"""
    import anthropic

    prompt = REFINE_PROMPT.format(
        podcast_name=metadata.get("podcast_name", "未知播客"),
        title=metadata.get("title", "未知标题"),
        date=metadata.get("date", ""),
        duration_minutes=metadata.get("duration_minutes", "未知"),
        transcript=transcript,
    )

    client = anthropic.Anthropic(api_key=llm_config["api_key"])

    print(f"  LLM 提炼中（{llm_config['provider']}/{llm_config['model']}）...")
    start = datetime.now()

    message = client.messages.create(
        model=llm_config["model"],
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    elapsed = (datetime.now() - start).total_seconds()
    print(f"  提炼完成，耗时 {elapsed:.0f} 秒")

    return message.content[0].text


def refine_with_openai(transcript, metadata, llm_config):
    """使用 OpenAI 兼容 API 提炼转录文本（支持 OpenAI / DeepSeek 等）"""
    from openai import OpenAI

    prompt = REFINE_PROMPT.format(
        podcast_name=metadata.get("podcast_name", "未知播客"),
        title=metadata.get("title", "未知标题"),
        date=metadata.get("date", ""),
        duration_minutes=metadata.get("duration_minutes", "未知"),
        transcript=transcript,
    )

    client_kwargs = {"api_key": llm_config["api_key"]}
    if llm_config.get("base_url"):
        client_kwargs["base_url"] = llm_config["base_url"]

    client = OpenAI(**client_kwargs)

    print(f"  LLM 提炼中（{llm_config['provider']}/{llm_config['model']}）...")
    start = datetime.now()

    response = client.chat.completions.create(
        model=llm_config["model"],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8192,
    )

    elapsed = (datetime.now() - start).total_seconds()
    print(f"  提炼完成，耗时 {elapsed:.0f} 秒")

    return response.choices[0].message.content


def refine_transcript(transcript, metadata, config):
    """路由到对应 LLM 后端进行提炼"""
    llm_config = get_llm_config(config)
    if not llm_config:
        return None

    try:
        if llm_config["provider"] == "anthropic":
            return refine_with_anthropic(transcript, metadata, llm_config)
        else:
            # openai / deepseek / 其他 OpenAI 兼容
            return refine_with_openai(transcript, metadata, llm_config)
    except Exception as e:
        print(f"  LLM 提炼失败: {e}")
        import traceback
        traceback.print_exc()
        return None


# ─── 命令处理 ────────────────────────────────────────────────────


def cmd_run(args):
    """运行完整 pipeline"""
    config = load_config()
    processed = load_processed()
    processed_guids = set(processed["processed_guids"])
    output_dir = Path(os.path.expanduser(config["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    model_name = config.get("whisper_model", "large-v3-turbo")
    min_dur = config.get("filter", {}).get("min_duration_minutes", 0) * 60
    max_eps = config.get("filter", {}).get("max_episodes_per_run", 5)

    # 检查 LLM 配置
    llm_config = get_llm_config(config)
    has_llm = llm_config is not None
    if has_llm:
        print(f"LLM 提炼已启用: {llm_config['provider']}/{llm_config['model']}")
    else:
        print("未配置 LLM，仅执行转录（提炼步骤跳过）")

    total_processed = 0
    results = []

    for podcast in config.get("podcasts", []):
        name = podcast["name"]
        rss_url = podcast["rss"]
        print(f"\n{'='*60}")
        print(f"播客: {name}")
        print(f"RSS: {rss_url}")

        try:
            feed = feedparser.parse(
                rss_url,
                request_headers={"User-Agent": "Mozilla/5.0 (podcast-pipeline)"},
            )
        except Exception as e:
            print(f"  RSS 解析失败: {e}")
            continue

        if not feed.entries:
            print("  无新内容")
            continue

        ep_count = 0
        for entry in feed.entries:
            if total_processed >= max_eps:
                print(f"  已达到本次最大处理数 ({max_eps})，停止")
                break

            guid = entry.get("id", entry.get("link", entry.get("title", "")))
            if guid in processed_guids:
                continue

            title = entry.get("title", "未知标题")
            duration = parse_duration(entry)
            pub_date = entry.get("published", "")

            if duration > 0 and duration < min_dur:
                print(f"  跳过（时长 {duration//60} 分钟 < {min_dur//60} 分钟）: {title}")
                continue

            audio_url = get_audio_url(entry)
            if not audio_url:
                print(f"  跳过（无音频链接）: {title}")
                continue

            print(f"\n  处理: {title}")
            print(f"  时长: {duration//60} 分钟")

            try:
                # 下载音频到临时目录
                with tempfile.TemporaryDirectory() as tmpdir:
                    ext = ".mp3"
                    if ".m4a" in audio_url:
                        ext = ".m4a"
                    audio_path = Path(tmpdir) / f"audio{ext}"
                    download_audio(audio_url, audio_path)

                    # 转录
                    result = transcribe_audio(audio_path, model_name)
                    transcript = format_transcript(result)

                    # 解析发布日期
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(pub_date)
                        date_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        date_str = datetime.now().strftime("%Y-%m-%d")

                    # 保存转录文本
                    safe_name = sanitize_filename(name)
                    safe_title = sanitize_filename(title)
                    transcript_filename = f"{date_str}_{safe_name}_{safe_title}.transcript.txt"
                    transcript_path = output_dir / transcript_filename
                    metadata = {
                        "podcast_name": name,
                        "title": title,
                        "date": date_str,
                        "duration_minutes": duration // 60 if duration > 0 else "unknown",
                        "guid": guid,
                    }
                    header = f"METADATA: {json.dumps(metadata, ensure_ascii=False)}\n\n"
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write(header + transcript)

                    print(f"  转录已保存: {transcript_path}")

                    # LLM 提炼
                    md_filename = f"{date_str}_{safe_name}_{safe_title}.md"
                    md_path = output_dir / md_filename

                    if has_llm:
                        refined = refine_transcript(transcript, metadata, config)
                        if refined:
                            with open(md_path, "w", encoding="utf-8") as f:
                                f.write(refined)
                            print(f"  提炼笔记已保存: {md_path}")

                    results.append({
                        "transcript_path": str(transcript_path),
                        "output_path": str(md_path),
                        "metadata": metadata,
                    })

                    # 标记已处理
                    processed_guids.add(guid)
                    total_processed += 1
                    ep_count += 1

            except Exception as e:
                print(f"  处理失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        if total_processed >= max_eps:
            break

    # 保存处理记录
    processed["processed_guids"] = list(processed_guids)
    save_processed(processed)

    # 输出结果 JSON 供 Claude 读取
    print(f"\n{'='*60}")
    print(f"本次共处理 {total_processed} 期")
    if results:
        print("\nPIPELINE_RESULTS_JSON:")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("没有新的节目需要处理")


def cmd_add(args):
    """添加新播客源"""
    url = args.url
    config = load_config()
    rsshub_base = config.get("rsshub_base", "https://rsshub.rssforever.com").rstrip("/")

    # 从小宇宙 URL 提取 podcast ID
    # 支持格式: https://www.xiaoyuzhoufm.com/podcast/XXXXX
    match = re.search(r"podcast/([a-f0-9]+)", url)
    if match:
        podcast_id = match.group(1)
        rss_url = f"{rsshub_base}/xiaoyuzhou/podcast/{podcast_id}"
    elif url.startswith("http") and "rss" in url:
        rss_url = url
        podcast_id = url.split("/")[-1]
    else:
        print(f"无法解析 URL: {url}")
        print("支持格式: https://www.xiaoyuzhoufm.com/podcast/<ID>")
        sys.exit(1)

    # 尝试获取播客名称
    print(f"正在获取播客信息...")
    try:
        feed = feedparser.parse(
            rss_url,
            request_headers={"User-Agent": "Mozilla/5.0 (podcast-pipeline)"},
        )
        name = feed.feed.get("title", f"播客_{podcast_id[:8]}")
    except Exception:
        name = f"播客_{podcast_id[:8]}"

    # 检查重复
    for p in config.get("podcasts", []):
        if p["rss"] == rss_url:
            print(f"播客已存在: {p['name']}")
            return

    config.setdefault("podcasts", []).append({"name": name, "rss": rss_url})
    save_config(config)
    print(f"已添加播客: {name}")
    print(f"RSS: {rss_url}")


def cmd_list(args):
    """列出已配置的播客"""
    config = load_config()
    podcasts = config.get("podcasts", [])
    if not podcasts:
        print("尚未配置任何播客")
        return
    print(f"已配置 {len(podcasts)} 个播客:\n")
    for i, p in enumerate(podcasts, 1):
        print(f"  {i}. {p['name']}")
        print(f"     RSS: {p['rss']}")
    print(f"\n过滤规则:")
    flt = config.get("filter", {})
    print(f"  最短时长: {flt.get('min_duration_minutes', 0)} 分钟")
    print(f"  每次最多: {flt.get('max_episodes_per_run', 5)} 期")
    print(f"输出目录: {config.get('output_dir', '~/Desktop/podcast-notes')}")

    # 显示 LLM 配置状态
    llm = config.get("llm")
    if llm and llm.get("provider"):
        print(f"\nLLM 提炼: {llm['provider']}/{llm.get('model', '默认')}")
    else:
        print(f"\nLLM 提炼: 未配置（仅转录）")


def cmd_process(args):
    """处理单个本地音频文件"""
    audio_path = Path(args.file).expanduser().resolve()
    if not audio_path.exists():
        print(f"文件不存在: {audio_path}")
        sys.exit(1)

    config = load_config()
    output_dir = Path(os.path.expanduser(config["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    model_name = config.get("whisper_model", "large-v3-turbo")

    print(f"处理本地音频: {audio_path}")
    result = transcribe_audio(audio_path, model_name)
    transcript = format_transcript(result)

    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_name = sanitize_filename(audio_path.stem)
    transcript_filename = f"{date_str}_{safe_name}.transcript.txt"
    transcript_path = output_dir / transcript_filename

    metadata = {
        "podcast_name": "本地音频",
        "title": audio_path.stem,
        "date": date_str,
        "duration_minutes": "unknown",
        "source_file": str(audio_path),
    }
    header = f"METADATA: {json.dumps(metadata, ensure_ascii=False)}\n\n"
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(header + transcript)

    md_filename = f"{date_str}_{safe_name}.md"
    md_path = output_dir / md_filename

    print(f"转录已保存: {transcript_path}")

    # LLM 提炼
    refined = refine_transcript(transcript, metadata, config)
    if refined:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(refined)
        print(f"提炼笔记已保存: {md_path}")

    print("\nPIPELINE_RESULTS_JSON:")
    result_data = [{
        "transcript_path": str(transcript_path),
        "output_path": str(md_path),
        "metadata": metadata,
    }]
    print(json.dumps(result_data, ensure_ascii=False, indent=2))


def cmd_refine(args):
    """对已有转录文本进行 LLM 提炼"""
    transcript_path = Path(args.file).expanduser().resolve()
    if not transcript_path.exists():
        print(f"文件不存在: {transcript_path}")
        sys.exit(1)

    config = load_config()
    llm_config = get_llm_config(config)
    if not llm_config:
        print("错误: 未配置 LLM。请在 config.yaml 中添加 llm 配置块，或设置对应环境变量。")
        sys.exit(1)

    # 读取转录文本
    with open(transcript_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析 metadata（如果有）
    metadata = {}
    if content.startswith("METADATA:"):
        first_line = content.split("\n", 1)[0]
        try:
            metadata = json.loads(first_line[len("METADATA:"):].strip())
        except json.JSONDecodeError:
            pass
        # 去掉 metadata 行
        content = content.split("\n\n", 1)[-1] if "\n\n" in content else content

    if not metadata:
        metadata = {
            "podcast_name": "未知播客",
            "title": transcript_path.stem.replace(".transcript", ""),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "duration_minutes": "未知",
        }

    print(f"提炼转录文本: {transcript_path}")
    print(f"LLM: {llm_config['provider']}/{llm_config['model']}")

    refined = refine_transcript(content, metadata, config)
    if not refined:
        print("提炼失败")
        sys.exit(1)

    # 输出到同目录，.transcript.txt → .md
    output_dir = args.output if args.output else None
    if output_dir:
        output_dir = Path(output_dir).expanduser().resolve()
    else:
        output_dir = transcript_path.parent

    md_filename = transcript_path.stem.replace(".transcript", "") + ".md"
    md_path = output_dir / md_filename

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(refined)

    print(f"提炼笔记已保存: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="Podcast Pipeline")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="运行完整 pipeline")
    subparsers.add_parser("list", help="查看已配置的播客")

    add_parser = subparsers.add_parser("add", help="添加新播客源")
    add_parser.add_argument("url", help="小宇宙播客 URL")

    process_parser = subparsers.add_parser("process", help="处理本地音频文件")
    process_parser.add_argument("file", help="音频文件路径")

    refine_parser = subparsers.add_parser("refine", help="对已有转录文本进行 LLM 提炼")
    refine_parser.add_argument("file", help="转录文本文件路径")
    refine_parser.add_argument("-o", "--output", help="输出目录（默认与输入文件同目录）")

    args = parser.parse_args()

    if args.command == "run" or args.command is None:
        cmd_run(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "process":
        cmd_process(args)
    elif args.command == "refine":
        cmd_refine(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Podcast Pipeline: RSS 抓取 → 音频下载 → mlx-whisper 转录 → 输出转录文本供 Claude 提炼"""

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

    total_processed = 0
    results = []

    for podcast in config.get("podcasts", []):
        name = podcast["name"]
        rss_url = podcast["rss"]
        print(f"\n{'='*60}")
        print(f"播客: {name}")
        print(f"RSS: {rss_url}")

        try:
            feed = feedparser.parse(rss_url)
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

                    # 输出结果供 Claude 读取
                    md_filename = f"{date_str}_{safe_name}_{safe_title}.md"
                    md_path = output_dir / md_filename
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
    # 从小宇宙 URL 提取 podcast ID
    # 支持格式: https://www.xiaoyuzhoufm.com/podcast/XXXXX
    match = re.search(r"podcast/([a-f0-9]+)", url)
    if match:
        podcast_id = match.group(1)
        rss_url = f"https://api.xiaoyuzhoufm.com/v1/podcast/rss/{podcast_id}"
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
        feed = feedparser.parse(rss_url)
        name = feed.feed.get("title", f"播客_{podcast_id[:8]}")
    except Exception:
        name = f"播客_{podcast_id[:8]}"

    config = load_config()
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
    print("\nPIPELINE_RESULTS_JSON:")
    result_data = [{
        "transcript_path": str(transcript_path),
        "output_path": str(md_path),
        "metadata": metadata,
    }]
    print(json.dumps(result_data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Podcast Pipeline")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="运行完整 pipeline")
    subparsers.add_parser("list", help="查看已配置的播客")

    add_parser = subparsers.add_parser("add", help="添加新播客源")
    add_parser.add_argument("url", help="小宇宙播客 URL")

    process_parser = subparsers.add_parser("process", help="处理本地音频文件")
    process_parser.add_argument("file", help="音频文件路径")

    args = parser.parse_args()

    if args.command == "run" or args.command is None:
        cmd_run(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "process":
        cmd_process(args)


if __name__ == "__main__":
    main()

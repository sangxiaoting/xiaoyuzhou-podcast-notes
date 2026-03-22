#!/bin/bash
# Podcast Pipeline 环境依赖安装脚本

set -e

echo "=== Podcast Pipeline Setup ==="

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi

echo "Python: $(python3 --version)"

# 安装 Python 依赖
echo "安装 Python 依赖..."
pip3 install mlx-whisper feedparser requests pyyaml anthropic openai

# 创建输出目录
mkdir -p ~/Desktop/podcast-notes

# 复制示例配置（如 config.yaml 不存在）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config.yaml.example" "$SCRIPT_DIR/config.yaml"
    echo "已从 config.yaml.example 创建 config.yaml，请根据需要编辑播客列表"
fi

# 初始化 processed.json
if [ ! -f "$SCRIPT_DIR/processed.json" ]; then
    echo '{"processed_guids": []}' > "$SCRIPT_DIR/processed.json"
    echo "已创建 processed.json"
fi

echo ""
echo "=== 安装完成 ==="
echo "输出目录: ~/Desktop/podcast-notes"
echo ""
echo "下一步："
echo "  1. 编辑 config.yaml 配置你关注的播客"
echo "  2. （可选）配置 LLM 提炼：在 config.yaml 中添加 llm 配置块"
echo "  3. 运行: python3 podcast_pipeline.py run"
echo "     或在 Claude Code 中执行 /podcast-pipeline"

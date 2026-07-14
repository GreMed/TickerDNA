#!/bin/bash
# TickerDNA 启动脚本
# 启动 Streamlit 服务并显示访问地址，不自动打开浏览器

cd "$(dirname "$0")"

echo "================================"
echo "  TickerDNA v0.2.0-beta1"
echo "================================"
echo ""
echo "正在启动 Streamlit 服务..."
echo ""
echo "访问地址: http://localhost:8526"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

# 检查虚拟环境
if [ -d ".venv" ]; then
    .venv/bin/python -m streamlit run app.py --server.port 8526 --server.headless true
else
    python3 -m streamlit run app.py --server.port 8526 --server.headless true
fi

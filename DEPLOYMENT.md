# TickerDNA 部署指南

## 架构概览

| 层级 | 平台 | 用途 |
|------|------|------|
| 源码托管 | GitHub | 保存干净的 TickerDNA 发布源码 |
| 应用运行 | Streamlit Community Cloud | 运行 TickerDNA Streamlit 应用 |
| 品牌入口 | Vercel | 承载简洁品牌入口页，链接到 Streamlit 应用 |

> 核心 Streamlit 应用运行在 Streamlit Community Cloud，不直接运行在 Vercel。

## Streamlit Community Cloud 部署

### 前置条件

- GitHub 仓库已创建并推送源码
- Streamlit Community Cloud 账号已注册（https://share.streamlit.io）

### 配置清单

| 配置项 | 值 |
|--------|-----|
| Repository | `<github-username>/TickerDNA` |
| Branch | `main` |
| Main file path | `app.py` |
| Python version | 3.12 |

### Secrets（在 Streamlit Cloud 管理面板设置）

| Secret 名称 | 说明 | 必填 |
|-------------|------|------|
| `SEC_USER_AGENT` | SEC EDGAR 请求的 User-Agent（格式：公司名 邮箱） | 是 |
| `OPENAI_API_KEY` | OpenAI API 密钥（用于辅助分析，可选） | 否 |
| `FM_DATA_CACHE_DIR` | 云端缓存目录，设置为 `/tmp/tickerdna_cache` | 是 |

> 不得把真实 Secrets 写入代码或提交到 GitHub 仓库。

### 部署步骤

1. 在 GitHub 创建仓库，推送 `release/TickerDNA-v0.2.0-beta1/` 目录内容到 `main` 分支
2. 登录 https://share.streamlit.io
3. 点击 "New app"
4. 选择 GitHub 仓库和分支
5. 设置 Main file path 为 `app.py`
6. 选择 Python 3.12
7. 在 Advanced Settings 中添加上述 Secrets
8. 点击 Deploy

### 云端缓存

Streamlit Cloud 的文件系统是临时性的，缓存目录设置为 `/tmp/tickerdna_cache`：

```
FM_DATA_CACHE_DIR=/tmp/tickerdna_cache
```

应用启动时会自动创建该目录。

## Vercel 品牌入口页

品牌入口页位于独立目录 `vercel-landing/`，是一个静态页面，包含：

- TickerDNA 品牌名称
- 一句话产品说明
- Apple、腾讯两个示范案例说明
- "开始体验 TickerDNA" 按钮（链接到 Streamlit 应用地址）
- 数据和投资风险提示

### 配置

体验按钮地址通过环境变量配置：

```
NEXT_PUBLIC_TICKERDNA_APP_URL=https://your-app.share.streamlit.app
```

> **重要：** 未配置真实地址时构建会失败，这是预期保护行为。
> 不得使用占位值或伪线上地址。

### 部署顺序

必须按以下顺序部署：

1. **先部署 Streamlit Cloud** — 按上文步骤完成 Streamlit 应用部署
2. **取得真实 TickerDNA URL** — 从 Streamlit Cloud 获取应用访问地址（如 `https://tickerdna.streamlit.app`）
3. **在 Vercel 设置环境变量** — 将 `NEXT_PUBLIC_TICKERDNA_APP_URL` 设置为上一步获取的真实地址
4. **Vercel Project Root Directory 设置为 `vercel-landing`** — 在 Vercel 项目设置中将 Root Directory 指向 `vercel-landing/` 子目录
5. **再部署入口页** — Vercel 会执行 `node build.js`，将 `__APP_URL__` 替换为真实地址后输出到 `dist/`

> 如果 `NEXT_PUBLIC_TICKERDNA_APP_URL` 未设置、为空或仍是占位值，构建会退出失败。
> 这是防止发布错误入口地址的保护行为。

## 本地运行

```bash
# 创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动应用
./start.command
# 或
streamlit run app.py --server.port 8526 --server.headless true
```

访问地址：http://localhost:8526

## 版本信息

- 当前版本：v0.2.0-beta1
- 发布日期：2026-07-15
- Python 版本：3.12

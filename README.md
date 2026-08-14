# Codex DeepSeek Vision

为 Codex + DeepSeek 工作流补充图片理解能力。这个 skill 会把本地图片或剪贴板图片发送给已配置的 Qwen 或 Gemini 视觉模型，再将结构化的视觉结果交给 DeepSeek 使用。

## 让 Agent 安装

在需要使用此 skill 的项目中，将下面这句话直接发给 Codex：

> 请使用 `$skill-installer` 安装 `jxcai/codex-deepseek-vision` 仓库根目录中的 skill，将它作为项目级 skill 安装到当前项目的 `.agents/skills` 目录，名称使用 `codex-deepseek-vision`。安装后读取 skill 的 `config.json`，根据 `active_provider` 在项目根目录创建 `.env`：使用 Qwen 时写入 `DASHSCOPE_API_KEY=`，使用 Gemini 时写入 `GEMINI_API_KEY=`，等号后的 value 保持为空。如果 `.env` 已存在，不要覆盖原有内容，只添加缺失的 key。完成后向用户提供可点击的 `.env` 文件链接，提示用户点击打开并在文件中填写 API key；不要要求用户把 API key 发到聊天中。

安装完成后，在下一轮对话中即可使用。仓库根目录就是 skill 根目录，因此安装时应使用仓库路径 `.`，不要再附加 `.agents/skills/codex-deepseek-vision`。

## 手动安装

在项目根目录执行：

```bash
mkdir -p .agents/skills
git clone https://github.com/jxcai/codex-deepseek-vision.git \
  .agents/skills/codex-deepseek-vision
```

目录结构应为：

```text
your-project/
├── .agents/
│   └── skills/
│       └── codex-deepseek-vision/
│           ├── SKILL.md
│           ├── agents/
│           ├── references/
│           ├── scripts/
│           └── config.json
└── .env
```

## 配置

1. 编辑 `config.json`，将 `active_provider` 设置为 `qwen` 或 `gemini`。
2. Agent 安装完成后会在项目根目录创建或补充 `.env`，并保留空 value：

```dotenv
# 使用 Qwen 时配置
DASHSCOPE_API_KEY=

# 使用 Gemini 时配置
GEMINI_API_KEY=
```

点击 Agent 返回的 `.env` 文件链接，在对应等号后填写 API key。不要提交 `.env`，也不要在聊天、提示词或日志中暴露密钥。

## 使用

安装后，可以直接让 Codex 使用 `$codex-deepseek-vision` 分析图片，例如：

```text
请使用 $codex-deepseek-vision 读取这张截图，并提取其中的错误信息。
```

也可以直接运行 bridge：

```bash
python3 .agents/skills/codex-deepseek-vision/scripts/vision_bridge.py \
  --image /absolute/path/to/image.png \
  --question '请说明图片中的主要内容。'
```

读取剪贴板图片：

```bash
python3 .agents/skills/codex-deepseek-vision/scripts/vision_bridge.py \
  --clipboard \
  --question '这张截图里显示了什么？'
```

脚本会把结果以 JSON 输出到标准输出，其中 `text` 字段是可交给 DeepSeek 的视觉上下文。

## 测试

```bash
cd .agents/skills/codex-deepseek-vision/scripts
python3 -m unittest -v test_vision_bridge.py
```

---
name: whiteboard-video-workflow
description: 从 SRT 字幕文件自动生成完整白板动画视频的端到端工作流。依次完成分镜解析、图片生成、视频生成三个阶段。当用户提供 SRT 文件并要求生成白板动画视频，或说"从字幕生成白板视频"、"白板视频工作流"时触发。
---

# Whiteboard Video Workflow

从 SRT 字幕文件到完整白板动画视频片段的自动化工作流。

## 输入参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `srtPath` | 是 | SRT 字幕文件的绝对路径 |
| `outputDir` | 否 | 输出根目录，默认为 SRT 文件所在目录 |

## 工作流步骤

整个流程分为 10 步，必须严格按顺序执行。步骤 3、5、7 使用 subagent，其余步骤由主 agent 直接执行。

### 步骤 0: 环境预检

运行本 skill 的 `scripts/check_env.py`，一次性检查所有依赖（Python 虚拟环境、RUNNINGHUB_API_KEY）：

```bash
python3 <skill-dir>/scripts/check_env.py
```

- 成功（退出码 0）：从输出中捕获 `PYTHON_PATH=<路径>`，**记录该路径用于步骤 7**，继续
- 失败（退出码 1）：从输出的 `ENV_RESULT=...` JSON 中解析各项检查结果，**向用户展示清晰易懂的错误说明和修复指引**（见下方故障排查指引），终止工作流

**注意：脚本会自动检测并安装可修复的依赖，只将无法自动修复的问题报告给大模型。**

#### 故障排查指引

当环境预检失败时，必须向用户清晰解释原因并给出具体修复步骤。根据失败项不同，给出对应说明：

**RUNNINGHUB_API_KEY 未配置：**

> 白板动画视频生成需要 RunningHub API 来调用 AI 模型先生成图片。你需要在 skill 目录下的 `.env` 文件中配置 API Key。
>
> 修复方法：
> 1. 在 `<skill-dir>/.env` 文件中添加一行：`RUNNINGHUB_API_KEY=你的API密钥`
> 2. 如果文件不存在，先创建它
> 3. API Key 可在 [RunningHub](https://www.runninghub.cn/) 获取
> 4. 配置完成后重新运行即可

**Python 依赖安装失败：**

> 白板动画依赖 OpenCV、NumPy、PyAV 等 Python 库来处理视频。自动安装未能成功。
>
> 修复方法：
> 1. 请确认你的 Python 版本为 3.9 或更高版本
> 2. 然后重新运行本工作流，脚本会再次尝试自动安装

**多项检查同时失败时**，逐条列出每个失败项及对应修复方法，便于用户一次性解决所有问题。

### 步骤 1: 确定输出目录

- 如果用户未指定 `outputDir`，则使用 `srtPath` 所在目录作为输出根目录
- 将 `outputDir` 转换为绝对路径

### 步骤 2: 创建输出目录结构

运行本 skill 的 `scripts/workflow_helper.py`：

```bash
python3 <skill-dir>/scripts/workflow_helper.py init-dirs "<outputDir>"
```

输出 JSON 含 `storyboardDir`、`imageDir`、`videoDir` 三个绝对路径，保存备用。

### 步骤 3: 解析 SRT 生成分镜脚本（subagent）

启动一个 **subagent**，指令为：

> 使用 Read 工具读取文件 `<将本 skill 目录替换为实际绝对路径>/references/storyboard-parser.md`，按照其中的工作流步骤执行。
>
> 输入参数：
> - srtPath = `<将srtPath替换为实际绝对路径>`
> - projectRoot = `<将storyboardDir替换为实际绝对路径>`
> - skill-dir = `<将本 skill 目录替换为实际绝对路径>`（用于定位脚本）
>
> 完成后返回 storyboard.json 的绝对路径和场景数量。
>
> **注意：主 agent 必须将实际路径值填入指令中，不要传递变量名，subagent 无法访问主 agent 的上下文。**

**必须等待 subagent 完成并获取 storyboard.json 路径后才继续。**

### 步骤 4: 解析 storyboard 生成图片提示词

运行本 skill 的 `scripts/workflow_helper.py`：

```bash
python3 <skill-dir>/scripts/workflow_helper.py gen-prompts "<storyboardJsonPath>"
```

输出一个 JSON 字符串数组，每个元素是一个带白板风格前缀的图片生成提示词，数组索引与 storyboard 中的 scenes 顺序一一对应。

同时从 storyboard.json 中提取每个 scene 的 `duration` 值（毫秒），按顺序记录为数组备用。

### 步骤 5: 批量生成白板图片（subagent）

启动一个 **subagent**，指令为：

> 使用 Read 工具读取文件 `<将本 skill 目录替换为实际绝对路径>/references/image-generator.md`，按照其中的工作流步骤执行。
>
> 使用批量模式，将以下 JSON 字符串数组作为 prompt 参数传入：
> `<将步骤4输出的提示词JSON数组的实际内容粘贴于此>`
>
> 参数：
> - skill-dir = `<将本 skill 目录替换为实际绝对路径>`（用于定位脚本）
> - 输出目录 = `<将imageDir替换为实际绝对路径>`
> - 宽高比 = "16:9"
>
> **注意：主 agent 必须将实际提示词内容和路径值填入指令中，不要传递变量名，subagent 无法访问主 agent 的上下文。**
>
> **重要：** 返回所有生成图片的路径列表，顺序必须与提示词数组顺序一致。

**必须等待 subagent 完成并获取所有图片路径后才继续。**

### 步骤 6: 校验图片顺序

确认步骤 5 返回的图片路径数组长度与 storyboard 的 scenes 数量一致，顺序正确（第 i 张图片对应第 i 个 scene）。

### 步骤 7: 批量生成白板动画视频片段（subagent）

启动一个 **subagent**，指令为：

> 调用 whiteboard-animation skill，使用批量模式。**跳过环境预检**（主 agent 已确认环境就绪），直接运行批量生成脚本：
>
> ```
> <将PYTHON_PATH替换为步骤0获取的实际值> <将whiteboard-animation skill目录替换为实际路径>/scripts/batch_generate.py \
>   --images <imagePaths[0]> <imagePaths[1]> ... \
>   --durations <durations[0]> <durations[1]> ... \
>   --output-dir <videoDir绝对路径>
> ```
>
> **主 agent 必须将 `PYTHON_PATH` 和 skill 目录的实际绝对路径填入指令中，不要传递变量名，subagent 无法访问主 agent 的上下文。**
>
> 参数：
> - `--images`：按分镜顺序排列的图片路径列表（空格分隔）
> - `--durations`：与图片一一对应的时长列表（单位：毫秒，直接使用 storyboard 中的 duration 值，无需转换）
> - `--output-dir`：`<videoDir绝对路径>`
>
> **重要：** 完成后，收集 `<videoDir>` 目录下所有生成的视频文件路径，按文件名时间戳排序，将完整的视频路径列表返回给主 agent。顺序必须与输入图片顺序一致。

**必须等待 subagent 完成并获取所有视频路径后才继续。**

### 步骤 8: 合并视频片段

运行本 skill 的 `scripts/workflow_helper.py`，将所有视频片段按顺序合并为一个完整视频。**必须使用步骤 0 获取的 `PYTHON_PATH`**（PyAV 依赖在虚拟环境中）：

```bash
<PYTHON_PATH> <skill-dir>/scripts/workflow_helper.py merge-videos "<outputDir>" <videoPath1> <videoPath2> ...
```

- 第一个参数为输出目录（合并后的视频保存在此目录）
- 后续参数为按分镜顺序排列的视频片段路径

输出 JSON 含 `mergedVideo`（合并后的视频绝对路径）、`totalSegments`、`sizeMB`。

### 步骤 9: 输出结果

输出最终结果，包含合并后的完整视频路径和所有片段信息：

输出格式示例：

```json
{
  "mergedVideo": "/path/to/output/白板视频_20260329_120000.mp4",
  "videoSegments": [
    "/path/to/video/vid_20260329_120000_h264.mp4",
    "/path/to/video/vid_20260329_120010_h264.mp4"
  ],
  "totalSegments": 2,
  "sizeMB": 15.3,
  "outputDir": "/path/to/output"
}
```

## 关键约束

- 步骤 0 必须在任何工作开始前执行，`check_env.py` 脚本会自动检测并安装可修复的依赖，只将无法修复的问题报告给大模型
- 步骤 3、5、7 必须使用 subagent 执行，主 agent 等待结果
- 步骤 0 获取的 `PYTHON_PATH` 必须传递给步骤 7 的 subagent 使用，**也用于步骤 8 的视频合并**（PyAV 依赖在虚拟环境中），避免重复环境检查
- 步骤 7 使用 whiteboard-animation 的批量模式（batch_generate.py），由 subagent 一次传入所有图片和时长，脚本内部串行生成，subagent 完成后返回所有视频路径
- 图片和视频的顺序必须与 storyboard 的 scenes 顺序严格对应
- duration 贯穿全链路使用毫秒，从 storyboard 到 batch_generate.py 再到 generate_whiteboard.py 统一为毫秒，避免浮点转换丢失精度
- 步骤 8 使用 PyAV（Python 库）合并视频片段，通过 H.264 重新编码输出统一格式的最终视频

## Resources

### references/

- `storyboard-parser.md` - SRT 分镜解析工作流指令，由步骤 3 的 subagent 读取执行
- `image-generator.md` - 图片生成工作流指令，由步骤 5 的 subagent 读取执行

### scripts/

- `check_env.py` - 一次性环境预检，检查 Python 虚拟环境、API Key，自动安装可修复的依赖
- `workflow_helper.py` - 提供 `init-dirs`（创建目录结构）、`gen-prompts`（解析 storyboard 生成提示词）、`merge-videos`（合并视频片段）三个子命令
- `generate-storyboard.py` - 解析 SRT + groups.json 生成 storyboard.json
- `generate-image.py` - 模型文生图，支持单张和批量并发模式
- `prompt_template.py` - 白板风格提示词模板

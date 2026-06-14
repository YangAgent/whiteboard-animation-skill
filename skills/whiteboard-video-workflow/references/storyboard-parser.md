# Storyboard Parser

将 SRT 字幕文件按语义分组，生成图片分镜脚本。

## 工作流程概览

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  读取 SRT 文件   │ --> │  AI 语义分组     │ --> │  JS 生成        │
│                 │     │  + 验证连续性    │     │  storyboard.json│
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**职责分离**：
- **AI 负责**：语义理解、分组判断、添加标签
- **JS 负责**：时间计算、数据生成（100% 精确）

## 目录定位（必须首先执行）

**禁止事项**：
- ❌ 禁止猜测项目路径
- ❌ 禁止在工具调用中使用相对路径

**正确做法**：
- ✅ 过程中生成的所有文件都保存在 `{projectRoot}` 绝对路径的文件夹下

## 输入

- `srtPath`（必传）: SRT 文件的绝对路径。如果未提供则直接报错终止，不允许继续执行。
- `projectRoot`（可选）: 项目根目录路径，所有输出文件将保存在此目录下。如果未传入，则使用 `srtPath` 所在的目录作为 `projectRoot`（即 `srtPath` 的父目录）。

## 输出

1. 中间文件 `{projectRoot}/groups.json`（AI 生成）
2. 最终文件 `{projectRoot}/storyboard.json`（JS 脚本生成）
3. 返回结构：
```json
{
  "storyboardPath": "/path/to/storyboard.json",
  "sceneCount": 17
}
```

---

## 步骤 1: 读取并分析 SRT 文件

使用 Read 工具读取 SRT 文件内容。

SRT 格式示例：
```
1
00:00:04,900 --> 00:00:07,000
下次展示作品或者讲方案时

2
00:00:07,000 --> 00:00:08,500
想让全场眼前一亮

3
00:00:08,500 --> 00:00:10,333
可以试试这样的动画演示稿
```

**记录 SRT 总条数**：统计字幕序号的最大值，后续验证需要用到。

---

## 步骤 2: 语义分组

根据以下规则将连续的字幕分组为场景：

### 分组原则（按优先级）

1. **语义完整性** - 不在句子中间切分，保持意思完整
2. **主题一致性** - 同一概念/话题的内容合并在一起
3. **时长控制** - 优先依据 SRT 的真实时间跨度分组，单场景尽量保持在 10-15 秒左右
4. **自然停顿** - 利用话题转换作为场景边界

### 时长控制细则

- 使用该组首条字幕的开始时间和末条字幕的结束时间，估算该组的实际时长
- **目标区间**：每组尽量控制在 10-15 秒
- **可接受偏差**：如果为了保持语义完整、避免在句子中间切分，可以略微短于 10 秒或长于 15 秒，但不要无理由偏离太多
- **过短处理**：如果某组明显短于 10 秒，且与前后场景主题连续、合并后仍然自然，优先与相邻场景合并
- **过长处理**：如果某组明显超过 15 秒，优先在自然停顿、转折词、举例引入、总结句等位置拆分
- 不要为了机械满足时长而破坏语义完整性，时长控制服从语义完整性与主题一致性

### 分组信号（强 - 应该开始新场景）

- 出现转折词："但是"、"然而"、"接下来"、"首先"、"其次"
- 出现总结词："总之"、"所以"、"因此"
- 出现引入词："比如"、"例如"、"举个例子"
- 话题明显切换

### 分组信号（弱 - 可以合并）

- 连续的列举项
- 同一句话被拆成多条字幕
- 问答对（问题和回答应在同一场景）

### 视觉提示

根据场景内容和上下文内容，为每个分组添加 `visualHint` 字段，作为视觉提示，
每个分组后续会根据内容生成图片，visualHint最主要的作用就是根据上下文，确定图片的基准视觉元素。
只描述画面中应该出现的视觉元素和构图，不要添加解释性说明。
例如：
visualHint: "图片元素包含..."

---

## 步骤 3: 输出 groups.json

生成的 JSON 格式：

```json
{
  "groups": [
    {
      "sceneId": "scene_001",
      "fromIndex": 1,
      "toIndex": 3,
      "visualHint": "图片元素包含..."
    },
    {
      "sceneId": "scene_002",
      "fromIndex": 4,
      "toIndex": 7,
      "visualHint": "图片元素包含..."
    }
  ]
}
```

**字段说明**：
- `sceneId`: 场景 ID，格式为 `scene_XXX`（3位数字，从 001 开始）
- `fromIndex`: 该场景包含的起始字幕序号（含）
- `toIndex`: 该场景包含的结束字幕序号（含）
- `visualHint`: 视觉提示字符串

使用 Write 工具将 groups.json 写入 `{projectRoot}/groups.json`。

---

## 步骤 4: 验证分组连续性（必须执行）

**在写入 groups.json 后，必须进行以下验证**：

### 验证规则

1. **起始验证**：第一个分组的 `fromIndex` 必须为 `1`
2. **连续性验证**：每个分组的 `fromIndex` 必须等于上一个分组的 `toIndex + 1`
3. **结尾验证**：最后一个分组的 `toIndex` 必须等于 SRT 总条数
4. **sceneId 格式**：必须是 `scene_XXX`，从 001 开始递增

### 自检方法

读取生成的 groups.json，逐项检查：

```
检查项 1: groups[0].fromIndex === 1 ?
检查项 2: 对于 i > 0，groups[i].fromIndex === groups[i-1].toIndex + 1 ?
检查项 3: groups[最后].toIndex === SRT总条数 ?
检查项 4: sceneId 是否连续 (scene_001, scene_002, ...)?
```

**如果验证失败**：
- 输出错误信息
- 修正 groups.json
- 重新验证直到通过

---

## 步骤 5: 运行 Python 脚本生成 storyboard.json

验证通过后，执行以下命令：

```bash
python3 <skill-dir>/scripts/generate-storyboard.py \
  "{srtPath}" \
  "{projectRoot}/groups.json" \
  "{projectRoot}/storyboard.json"
```

**注意**：`<skill-dir>` 是 `whiteboard-video-workflow` skill 的绝对路径，由主 agent 在 subagent 指令中提供。

脚本会：
1. 解析 SRT 文件提取时间信息
2. 再次验证 groups.json 的完整性
3. 计算每个场景的 startTime、duration
4. 计算每个 segment 的 relativeStart、relativeDuration
5. 生成完整的 storyboard.json

### 脚本输出

成功时输出：
```
✅ 生成完成!
   - 场景数量: 17
   - 总时长: 136.5s
   - 输出文件: /path/to/storyboard.json

__RESULT_JSON__
{"success":true,"storyboardPath":"/path/to/storyboard.json","sceneCount":17,"totalDuration":136466}
```

失败时会输出错误信息并以非零状态码退出。

---

## 步骤 6: 返回结果

从脚本输出中提取结果，返回：

```json
{
  "storyboardPath": "{projectRoot}/storyboard.json",
  "sceneCount": 17
}
```

---

## 执行清单

1. [ ] 从 prompt 中提取 `srtPath`（必传，缺失则报错终止）；提取 `projectRoot`（可选，未传入则取 `srtPath` 的父目录）
2. [ ] 读取 SRT 文件，记录总条数
3. [ ] 根据语义规则进行分组
4. [ ] 为每个分组添加 visualHint
5. [ ] 生成 groups.json 并写入文件
6. [ ] **验证分组连续性**（起始为1、连续、结尾完整）
7. [ ] 如果验证失败，修正后重新验证
8. [ ] 运行 `<skill-dir>/scripts/generate-storyboard.py` 脚本
9. [ ] 确认脚本执行成功
10. [ ] 返回 `{ storyboardPath, sceneCount }`

---

## 示例

### 输入 SRT（部分）

```
1
00:00:04,900 --> 00:00:07,000
下次展示作品或者讲方案时

2
00:00:07,000 --> 00:00:08,500
想让全场眼前一亮

3
00:00:08,500 --> 00:00:10,333
可以试试这样的动画演示稿

4
00:00:10,600 --> 00:00:11,233
当其他人

5
00:00:11,233 --> 00:00:13,433
还在一页页翻着静态幻灯片时

6
00:00:13,433 --> 00:00:14,700
你按下播放键

7
00:00:14,700 --> 00:00:16,533
所有目光都被你的画面吸引
```

### 输出 groups.json

```json
{
  "groups": [
    {
      "sceneId": "scene_001",
      "fromIndex": 1,
      "toIndex": 3,
      "visualHint": "图片元素包含..."
    },
    {
      "sceneId": "scene_002",
      "fromIndex": 4,
      "toIndex": 7,
      "visualHint": "图片元素包含..."
    }
  ]
}
```

### 验证检查

```
✅ groups[0].fromIndex = 1 (正确)
✅ groups[1].fromIndex = 4 = groups[0].toIndex(3) + 1 (连续)
✅ groups[最后].toIndex = 7 = SRT总条数 (假设只有7条)
✅ sceneId 连续: scene_001, scene_002
```

### 最终 storyboard.json（由 JS 脚本生成）

```json
{
  "totalDuration": 11633,
  "sceneCount": 2,
  "scenes": [
    {
      "id": "scene_001",
      "startTime": 4900,
      "duration": 5433,
      "segments": [
        {
          "text": "下次展示作品或者讲方案时",
          "relativeStart": 0,
          "relativeDuration": 2100
        },
        {
          "text": "想让全场眼前一亮",
          "relativeStart": 2100,
          "relativeDuration": 1500
        },
        {
          "text": "可以试试这样的动画演示稿",
          "relativeStart": 3600,
          "relativeDuration": 1833
        }
      ],
      "visualHint": "图片元素包含..."
    },
    {
      "id": "scene_002",
      "startTime": 10600,
      "duration": 5933,
      "segments": [
        {
          "text": "当其他人",
          "relativeStart": 0,
          "relativeDuration": 633
        },
        {
          "text": "还在一页页翻着静态幻灯片时",
          "relativeStart": 633,
          "relativeDuration": 2200
        },
        {
          "text": "你按下播放键",
          "relativeStart": 2833,
          "relativeDuration": 1267
        },
        {
          "text": "所有目光都被你的画面吸引",
          "relativeStart": 4100,
          "relativeDuration": 1833
        }
      ],
      "visualHint": "图片元素包含..."
    }
  ]
}
```

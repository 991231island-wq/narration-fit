---
name: narration-fit
description: "视频已剪好，但文案和每段时长对不上时使用。输入已剪时间轴标注、现有口播或旁白全文，以及用户选定的语速。按「段时长 × 语速」计算每段字数预算。策略三选一：trim 删到能念完，keep_faster 尽量留字并接受更快语速，expand 在预算内补满偏短段。输出逐段对照和可再改的 JSON 与 HTML。适用科普口播、读文章旁白、纪录片解说、课程讲稿。不读视频、不做 TTS、不从零写整片；本地脚本校验，无需 Key。触发词：口播优化、文案对时长、念不完、稿太短、扩写、填时长、补口播、按段改稿、语速预算、旁白对时间轴、读文章改文案、旁白对结构。不用于文风改写、从知识点生成分镜、成片渲染、自动精剪或短剧防串脸。"
license: MIT
metadata:
  version: "1.1.0"
  license: MIT
  category: ai-media
  tags:
    - 口播优化
    - 语速预算
    - 时间轴
---

# 口播对轴改稿（narration-fit）

视频已剪好，文案和每段时长对不上时用这个 Skill。输入已剪时间轴标注、现有口播或旁白全文，以及语速。它按「段时长 × 语速」算每段字数预算，再按 trim（删到能念完）、keep_faster（尽量留字）或 expand（在预算内补满偏短段）改稿，输出逐段对照。适用科普口播、读文章旁白、纪录片解说、课程讲稿。不读视频，不做 TTS，不从零写整片。本地脚本校验，无需 Key。

中文展示名是「口播对轴改稿」。包名 `narration-fit`。许可证 MIT。分类 `ai-media`。版本 1.1.0。

## 红线

- v1 **不读** mp4、mov 或其他视频二进制，不抽帧，不做 ASR。
- 不做 TTS，不渲染成片，不调用付费云 API，不需要任何 Key。
- 不从知识点、卖点或主题从零写分镜。结构标注是输入，不是输出。
- 不自动下单、支付、发布、评论、点赞或登录第三方。
- 不把澄象语料、成片或未公开 schema 写进包。公开发布示例用本包原创的现代中文，并注明「示例文案为本 Skill 原创，仅供演示」。不要把未授权的现代名家散文打进公开发布包。用户自备有权文本可以在本地测。
- 语速只使用用户给出的数字，并原样写进报告。脚本不会偷偷把 4.7 改成 5.5 来让校验通过。
- `expand` 只在该段预算以内补画面解释、过渡和被压缩的信息。不换文风，不从零另写一篇，不编造与画面或原文无关的事实。
- 预算、字数、超不超，只信 `scripts/validate_narration_fit.py` 的重算结果。模型自己口算的字数不能当验收。
- 语义是否还准确，脚本不保证。必留句和用户抽查才是验收手段。

## 什么时候用

用户同时具备下面几样，就用本 Skill：

- 已剪好的结构标注：`timeline.json`，或能改写成该文件的等价标注
- 现有文案：口播稿、读文章旁白、纪录片解说或课程讲稿，`draft.txt` 或已粗分到段的 `draft.json`
- 一个明确策略：`trim`、`keep_faster` 或 `expand`
- 一个明确语速：字／秒，由用户选定

触发说法包括：口播优化、文案对时长、这段念不完、稿太短、扩写、填时长、补口播、按段改稿、语速预算、旁白对时间轴、读文章改文案、旁白对结构。

## 什么时候不用

- 还没有时间轴，想从知识点生成分镜
- 想从卖点直接写一条新口播
- 想合成语音、对轴字幕烧录、自动删停顿或导出成片
- 短剧台词诊断、防串脸、锁站位
- 用户只有成片、没有文案：请用户先用上游转写 Skill，本包不调用

## 输入

细节见 `references/timeline-schema.md`、`references/fit-schema.md`、`references/pacing.md`。

1. `timeline.json`：每段至少有 `segment_id`、`start_s`、`end_s`、`visual`。可选 `label`。
2. 全文文案。模型负责对齐；对齐结果写入 `fit.json` 的 `original`。没地方放的句子放进 `unassigned`，禁止删掉不报。
3. `strategy`：`trim`、`keep_faster` 或 `expand`。
4. `chars_per_sec`：必填。用户没给数字时，先问，不要自己填。
5. 可选 `must_keep`：必须原句留在某一段改后文案里的短句。
6. 可选 `max_chars_per_sec_cap`：默认 `6.0`。这是产品默认上限，是否合理未核实。

用户还没选定语速时，可以这样说明建议值，然后等用户选：

- 舒适档建议 **4.7** 字／秒。出处是澄象 README 的参考值，不是行业标准。
- 加快档建议 **5.5** 字／秒。这是本 Skill 的产品默认，不是实测。
- `trim` 配舒适语速，`keep_faster` 配用户接受的更快语速。

## 工作流

完整失败分支见 `references/workflow.md`。

1. 读时间轴和全文。时间轴不合法就停，把脚本报错给用户，不要先改稿。
2. 把全文逐句划进 `segment_id`。`original` 必须是原文切片，不要先改写再冒充原文。对不上的句子整句放进 `unassigned`。
3. 按策略改 `revised`。没被点名的段，`revised` 可以等于 `original`。
4. `trim`：语速锁死在用户选定的值上。超了就删减或合并句式，优先保住 `must_keep`。不要用加快语速来过关。
5. `keep_faster`：用用户选定的更高语速算预算，尽量留信息。仍然超，就按脚本提示再删，或者告诉用户「要提到 X 字／秒」；X 不得超过上限。不要无上限加速。
6. `expand`：画面很长、稿子很短、念完会空一段时间时用。默认当原文少于预算的 70%（`expand_short_ratio`，可改）就扩写。只补画面里看得到的过程、过渡，以及原文已经压缩掉的信息，补完仍须 `chars ≤ budget`。某段若已经超预算，先压回预算内，报告会标「先修剪」，不要用加字掩盖超段。
7. 运行校验脚本。未通过就只改失败段的 `revised`，再跑。不要重切视频。
8. 运行渲染脚本，把 `fit.json`、对照 HTML、校验报告交给用户。

## 命令

在 Skill 根目录执行。只依赖 Python 3 标准库，断网也能跑。

```bash
python3 scripts/validate_narration_fit.py \
  --timeline examples/demo-timeline.json \
  --fit examples/demo-fit-trim.json \
  --report validation-report.md \
  --json-out validation-report.json

python3 scripts/render_diff.py \
  --timeline examples/demo-timeline.json \
  --fit examples/demo-fit-trim.json \
  --html diff.html \
  --report validation-report.md
```

退出码：`0` 通过，`1` 预算或时间轴或必留句未通过，`2` 文件或 JSON 读不了。

只改某一段时，改该段的 `revised`，用同一条命令重跑。其它段的预算行数字应保持不变；合计可以变。

## 输出

- `fit.json`：每段 `original`、`revised`，外加脚本重算的字数、预算、`status`
- `diff.html`：逐段对照。删除线是删掉的字，绿色底是新增的字。页眉必须能看见本次语速
- `validation-report.md`：逐段数字、必留句落点、未分配原文、本次语速

下一步只建议、不代劳：用户若没有文案，可先看转写 Skill；若要出声，另选 TTS。链接写在 `references/upstream-downstream.md`。本包不安装、不调用。

## 字数与预算

- 字数：去掉空白后的字符数。v1 **标点计入**。
- 预算：`floor((end_s - start_s) × chars_per_sec)`，十进制计算后向下取整。
- `trim` 下任一段改后字数大于预算，校验失败。
- `keep_faster` 下同样用用户给的语速算预算；通过时报告和 HTML 都打印该语速。仍超则失败，并写出还要删多少字，或语速要提到多少；超过上限就只能再删。
- `expand` 下改后同样不能超过预算。报告写出 `under_by` 和处理结果（已扩写、先修剪、仍偏短、保持）。仍偏短只警告，不因此失败，避免把故意留白判死；超预算一定失败。
- `unassigned` 非空默认失败，防止把多余句子扔进桶里假装通过。原文会打印在报告里。

人工抽查清单见 `references/checklist.md`。

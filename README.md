# 口播对轴改稿（narration-fit）

版本 1.1.0。MIT License，版权署名为 2026 991231island-wq。

这个 Skill 面向已经剪好的视频：把已有口播或旁白对齐到用户提供的分段时间轴，按用户选定的语速计算字数预算，并逐段改稿。它适用于科普口播、读文章旁白、纪录片解说和课程讲稿。

## 输入与输出

需要一份带有段落 ID、起止时间和画面说明的时间轴，一份现有文案，以及用户明确选择的策略和语速。可选提供必须原句保留的短句。

结果包括逐段原文与改后文案、字数和预算、校验报告，以及可在浏览器打开的独立 HTML 差异页。HTML 以删除线标出删减、绿色底标出新增，并显示本次语速。

## 三种改稿策略

- trim：按给定语速删改到不超过每段预算，不自动加快。
- keep_faster：用户明确选择较快语速后，尽量保留文案；超预算仍会失败。
- expand：原文明显偏短时补充与画面相关的过渡或原文已压缩的信息；不能超过预算。已经超预算的段落先压回预算。

Agent 根据原文、画面说明和用户要求负责对齐与改写。脚本只负责重算字数预算、检查结构和限制、生成报告与 HTML；脚本不会生成口播稿，也不会判断语义是否准确。

## 运行

本次在 Python 3.12.10 上验证，脚本仅使用 Python 标准库。其他 Python 版本尚未验证。命令从 Skill 根目录运行：

    python scripts/validate_narration_fit.py --timeline examples/demo-timeline.json --fit examples/demo-fit-trim.json --report validation-report.md --json-out validation-report.json

    python scripts/render_diff.py --timeline examples/demo-timeline.json --fit examples/demo-fit-trim.json --html diff.html --report validation-report.md

将文件名中的 demo-fit-trim.json 换成 demo-fit-keep-faster.json 或 demo-fit-expand.json，可运行另外两种示例。脚本退出码为 0（通过）、1（输入数据不符合预算或约束）、2（文件或 JSON 无法读取）。报告目录需预先存在；HTML 输出目录会自动创建。

在发布候选源码目录运行标准库回归测试：

    python -m unittest discover -s tests -v

本地脚本不需要 API Key。若使用宿主 Agent 生成或改写文案，宿主模型是否收费由对应服务决定，“脚本无需 Key”不代表模型服务免费。

## 本地 Gradio 工作室

`studio/` 是浏览器里的口播对轴改稿，和 Skill 并列，不替换 `scripts/`。校验和对照页调用仓库里的 `scripts/validate_narration_fit.py`、`scripts/render_diff.py`。模型只负责对齐和改写，字数预算只由脚本重算。

依赖只服务工作室，Skill 脚本仍然只用标准库。下面这组在 Python 3.12、gradio 5.50、openai 2.54 上跑通过：

    python -m pip install -r studio/requirements.txt
    python studio/app.py

进程监听 `0.0.0.0:7860`。本机打开 http://127.0.0.1:7860 。

页面打开时会放上示例《馒头为什么会胀起来》的预置通过结果，只跑本地脚本，所以没有令牌时首屏也不是空的。「一键演示」会重新载入这份示例。「开始改稿」才会请求魔搭。令牌不要写进代码或页面，只在启动这个进程的终端里设置：

    export MODELSCOPE_API_TOKEN=你的令牌

推理地址是 `https://api-inference.modelscope.cn/v1`，模型是 `Qwen/Qwen3.5-35B-A3B`（OpenAI 兼容 Chat Completions）。

不调模型、只检查脚本链路：

    cd studio
    python -m studio_lib.pipeline

退出码 0 表示示例 trim 校验通过，并在临时目录写出 `fit.json`、`diff.html`、`validation-report.md`。

工作室里可以改某一段的改后文案，再点「重验此段」或「重验全部」。这两下只跑脚本。步骤会显示对齐、校验、改稿第 N 段、再校验。缺令牌、输入不合法或模型 JSON 不合规时，页面给中文原因和改法，不贴调用栈。

已知限制：对齐和改稿质量取决于模型；单段 JSON 失败会留下改前文字并继续；默认最多两轮改稿，不保证通过。语义、必留句是否合适仍要人工看。不读视频，不做配音。预置结果是示例，不是用户自己的稿。

## 使用边界

字数预算是按段时长乘以用户选择的字／秒后向下取整得到的检查值，不等于真实发声时长。停顿、情绪、发音、专名、数字读法和语速差异都会影响实际口播，需要人工试听或抽查。

本工具不读取视频或抽帧，不做 ASR 转写、TTS 配音、剪辑或成片渲染，也不调用网络服务。结构、数字、专名、事实与语义仍需人工核对；脚本通过只表示结构和预算规则通过。

示例素材用于演示，不代表真实口播测试结果。舒适语速建议、加快语速默认值和上限属于产品参考值，不是适用于所有口音的行业标准。

## 版本说明

Skill 产品版本为 1.1.0，放在 SKILL.md 的 metadata.version。示例 fit JSON 中的 version 是数据格式版本，独立于 Skill 产品版本；不要将其自动改写成 1.1.0。

## 目录

- SKILL.md：Agent 工作流、边界和约束。
- scripts/：字数预算校验器与差异页渲染器。
- references/：时间轴、fit 数据格式、语速、工作流和人工抽查说明。
- examples/：三种策略的输入示例。
- tests/：本地可重复运行的标准库回归测试；发布 ZIP 不包含测试文件。
- studio/：本地 Gradio 工作室。依赖见 `studio/requirements.txt`。

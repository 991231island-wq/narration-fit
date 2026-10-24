# 上游与下游

本 Skill 不调用、不安装下面任何一项。用户若需要，自己打开链接。

v1 的 MCP 配置是空的：

```json
{ "mcpServers": {} }
```

## 只有成片、没有文案

先转写，再把文本交给本 Skill。

- 抖音口播转文字：https://www.modelscope.cn/skills/kshin1982/douyin-transcriber
- 抖音文案与逐字稿提取：https://www.modelscope.cn/skills/socialhelper/douyin-video-copy-extract

这两条是 2026-10-23 能在魔搭 OpenAPI 查到的邻近 Skill，不是本包的依赖。它们可能要本地模型或云 API；那是它们的边界，不要把 Key 写进 narration-fit。

## 文案已经卡进时长、想出声

TTS 是下游。本包停在文案和字数预算。

- 文本转语音示例：https://www.modelscope.cn/skills/kissstrong/text2voice

不要在本 Skill 的步骤里调用它，也不要假设用户装了音色或 Key。

## 想改片子本身

去废话、删停顿、重新卡点，都是剪辑工具的事。本 Skill 只改口播文字。时间不够放必留句时，请用户自己加长那一段，再把新的 `start_s` / `end_s` 交回来。

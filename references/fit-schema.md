# fit.json

模型写对齐和改稿，脚本重算数字。预填的 `chars_*` 或 `budget` 如果和脚本不一致，报告会警告，并以脚本为准。

```json
{
  "version": "1.0.0",
  "title": "馒头为什么会胀起来",
  "strategy": "trim",
  "chars_per_sec": 4.7,
  "max_chars_per_sec_cap": 6.0,
  "must_keep": ["气孔是发酵留下的，不是后来吹进去的"],
  "unassigned": "",
  "segments": [
    {
      "segment_id": "s1",
      "original": "对齐后的原文切片，不改字",
      "revised": "按策略改过的口播",
      "note": "可选：删了哪一层信息"
    }
  ]
}
```

| 字段 | 谁写 | 规则 |
|---|---|---|
| `strategy` | 用户选定，模型原样写入 | `trim`、`keep_faster` 或 `expand` |
| `expand_short_ratio` | 可选 | 默认 `0.7`。原文字数 &lt; 预算 × 该值时，expand 把这段视为明显偏短 |
| `chars_per_sec` | 用户选定 | 必填，大于 0，且不得超过上限 |
| `max_chars_per_sec_cap` | 可选 | 缺省 `6.0`。产品默认，未核实是否适合所有口音 |
| `must_keep` | 用户 | 字符串数组。每一句必须完整出现在**同一段**的 `revised` 里。匹配时去掉空白，标点要一致 |
| `unassigned` | 模型 | 没划入任何段的原文。默认非空即失败，报告会印出原文 |
| `original` | 模型 | 该段对应的原文切片，保持原句，方便对照删了什么 |
| `revised` | 模型 | 准备拿去念的文案。校验只看它的字数 |
| `note` | 可选 | 给人看的删改说明，不参与计数 |
| `chars_original` / `chars_revised` / `budget` / `status` | 脚本 | 可写回文件，但是验收以脚本重算为准 |

`status` 只有两种：`ok`（改后字数 ≤ 预算）、`over`（大于预算）。欠多少字看 `under_by`，欠预算本身不算失败。

段落集合必须和时间轴的 `segment_id` 一致：缺一段失败，多一段也失败。

只改一段时，只动该段的 `revised`（和可选的 `note`）。不要重排 `segment_id`，不要改其它段的文字。

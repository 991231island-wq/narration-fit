"""把失败说成人话：原因和怎么修。页面上不放调用栈。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

TOKEN_ENV = "MODELSCOPE_API_TOKEN"


class TokenMissingError(RuntimeError):
    """未配置 MODELSCOPE_API_TOKEN。"""


class ModelJSONError(RuntimeError):
    """模型输出无法解析为约定 JSON。"""


@dataclass(frozen=True)
class HumanError:
    title: str
    reason: str
    fix: str

    def markdown(self) -> str:
        return (
            f"### {self.title}\n\n"
            f"**原因**\n\n{self.reason}\n\n"
            f"**怎么修**\n\n{self.fix}\n"
        )


def redact(text: str) -> str:
    """去掉可能夹带的令牌，并截掉调用栈。"""
    raw = text or ""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token and token in raw:
        raw = raw.replace(token, "〔已隐藏〕")
    raw = re.sub(r"(?:sk|ms)-[A-Za-z0-9_\-]{6,}", "〔已隐藏〕", raw)
    if "Traceback (most recent call last)" in raw:
        return "内部错误，调用栈已隐藏。"
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    return line[:240]


def _input_fix(message: str) -> str:
    rules = (
        (
            ("时间轴为空", "请上传"),
            "上传 timeline.json，或把 JSON 贴到时间轴文本框。也可以先点「一键演示」看一份能跑的示例。",
        ),
        (
            ("无法解析", "JSON"),
            "检查括号、引号和末尾逗号。时间轴是一个 JSON 对象，里面要有 segments 数组。",
        ),
        (
            ("口播全文为空", "draft"),
            "上传 .txt，或把口播贴到全文框。点「一键演示」会填入示例原稿。",
        ),
        (
            ("UTF-8", "编码"),
            "用编辑器把文件另存为 UTF-8，或直接粘贴文本，不要上传二进制文件。",
        ),
        (
            ("strategy", "策略"),
            "策略只选 trim、keep_faster、expand 其中一个。",
        ),
        (
            ("chars_per_sec", "语速"),
            "语速填大于 0 的数字，例如 4.7。脚本不会替你改这个数。",
        ),
        (
            ("expand_short_ratio", "偏短"),
            "偏短阈值填 0 到 1 之间的小数，常用 0.7。只有 expand 会用到它。",
        ),
        (
            ("时间轴不合法", "segment_id", "visual", "重叠"),
            "每段都要有 segment_id、start_s、end_s 和 visual。时间可以首尾相接，不能重叠。先改时间轴，再改稿。",
        ),
        (
            ("找不到段落",),
            "在「选择段落」里点一段，再重验。若下拉是空的，先点「一键演示」或「开始改稿」。",
        ),
        (
            ("请先选择",),
            "先在下拉框里选定一段，再点「重验此段」。看全部预算用「重验全部」。",
        ),
    )
    for keys, fix in rules:
        if any(key in message for key in keys):
            return fix
    return "按上面的原因改输入后再试。点「一键演示」可以回到一份已知能通过的示例。"


def advice_for_report(errors: list[str], *, strategy: str) -> str:
    text = "\n".join(errors)
    if "必留句" in text:
        return "必留句要整句出现在同一段的改后文案里（去掉空白后仍能对上，标点要一致）。把原句贴回去，或删掉这句必留句，再点重验。"
    if "未分配" in text:
        return "还有句子没划进任何段。把它们写进某一段，或确认不要之后清空未分配，再重验。不要为了过关把超预算的句子丢进未分配。"
    if "超预算" in text or "超过上限" in text:
        if strategy == "trim":
            return "trim 锁死当前语速，不会自动加快。把超预算的段再删短，然后点「重验此段」。这一步只跑本地脚本。"
        if strategy == "keep_faster":
            return "可以再删超出来的字，或把语速调高（不要超过上限）后点「重验全部」。加快只按你填的数字算，脚本不会偷偷加。"
        return "expand 也不能超过预算。超了的段先删回预算内，偏短的段再补画面里看得到的信息，然后重验。"
    if "仍偏短" in text:
        return "欠预算本身不会失败。若希望补满，用 expand 改这一段；只是想确认数字，点「重验全部」即可。"
    return "按问题列表改对应段的文案，再点「重验此段」或「重验全部」。重验不调用模型。"


def humanize(exc: BaseException) -> HumanError:
    """把异常收成标题、原因、怎么修。不返回 traceback。"""
    name = type(exc).__name__
    detail = redact(str(exc))

    if isinstance(exc, TokenMissingError) or name == "TokenMissingError":
        return HumanError(
            title="还不能调用模型",
            reason=f"没有读到环境变量 `{TOKEN_ENV}`。改稿需要它访问魔搭推理，页面不会保存或显示令牌。",
            fix=(
                "在**启动本应用的同一个终端**里设置令牌，然后重新运行 `python studio/app.py`，再点「开始改稿」。\n\n"
                "```bash\n"
                f"export {TOKEN_ENV}=你的令牌\n"
                "```\n\n"
                "不要把令牌写进代码、页面或聊天。没有令牌时，第 4 步会写明原因，并留下对齐之后、尚未改写的校验。改文案后可以点「重验」，那一步只跑本地脚本。"
            ),
        )

    if isinstance(exc, ModelJSONError) or name == "ModelJSONError":
        reason = "模型没有按约定返回一个 JSON 对象。"
        if detail and detail not in reason:
            reason = f"{reason} {detail}"
        return HumanError(
            title="模型返回的内容没法用",
            reason=reason,
            fix=(
                "再点一次「开始改稿」。程序会自动要求模型重答一次；仍失败就先缩短口播，或确认网络能访问魔搭。"
                "字数是否超预算只看本地脚本，不用模型自己报的数字。"
            ),
        )

    if name == "InputError" or type(exc).__name__ == "InputError":
        reason = detail or "输入不完整，脚本拒绝继续。"
        return HumanError(
            title="输入还不能跑",
            reason=reason,
            fix=_input_fix(reason),
        )

    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return HumanError(
            title="魔搭没有接受这个令牌",
            reason="推理接口拒绝了当前令牌（登录失败或没有权限）。页面没有展示令牌本身。",
            fix=(
                "到魔搭确认令牌仍然有效、且能调用推理接口。在启动终端里重新设置 "
                f"`{TOKEN_ENV}`，重启 `python studio/app.py`。不要把令牌贴到页面上。"
            ),
        )

    if name == "NotFoundError":
        return HumanError(
            title="推理接口找不到这个模型",
            reason="当前请求的模型是 `Qwen/Qwen3.5-35B-A3B`。接口返回找不到模型或没有该模型的权限。",
            fix="确认账号可以调用这个模型后再试。本应用不会自动换成别的模型。",
        )

    if name in {"APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout"}:
        return HumanError(
            title="连不上魔搭推理",
            reason="访问 `https://api-inference.modelscope.cn/v1` 失败或超时。稿子还没有被模型改写。",
            fix="检查本机网络和代理，确认能打开魔搭推理接口后再点「开始改稿」。本地的预置结果和重验不需要网络。",
        )

    if name == "RateLimitError":
        return HumanError(
            title="魔搭暂时限流了",
            reason="推理请求太密，接口拒绝继续。这一轮改稿没有写回。",
            fix="等一会儿再点「开始改稿」。本地重验不受限流影响。",
        )

    if name in {"APIStatusError", "InternalServerError", "BadRequestError", "APIError"}:
        extra = f"（{detail}）" if detail else ""
        return HumanError(
            title="魔搭推理没有完成",
            reason=f"接口返回了错误，模型改稿中断{extra}。",
            fix="稍后重试。若一直失败，先用「一键演示」确认本地脚本正常，再检查令牌和网络。",
        )

    if name == "OSError":
        return HumanError(
            title="本地文件没有读到",
            reason=detail or "读取模板或示例文件失败。",
            fix="在仓库根目录运行 `python studio/app.py`，不要把 `studio/app.py` 单独拷走。对照模板在 `assets/diff-template.html`。",
        )

    reason = f"出现没有单独命名的问题（{name}）。"
    if detail:
        reason = f"{reason} {detail}"
    return HumanError(
        title="这一步没有完成",
        reason=reason,
        fix="看上面的原因改输入或环境。点「一键演示」可以确认工作室本身还能跑。页面不展开调用栈。",
    )

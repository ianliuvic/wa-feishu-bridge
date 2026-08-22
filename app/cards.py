"""Feishu interactive card builders for forwarded WhatsApp messages."""

from datetime import datetime, timedelta, timezone

from .config import FEISHU_CARD_FOOTER, FEISHU_CARD_TEMPLATE, FEISHU_CARD_TITLE
from .evolution import EvolutionMessage

SHANGHAI_TZ = timezone(timedelta(hours=8))


def _ts() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _meta_markdown(evt: EvolutionMessage, note: str = "") -> str:
    """Metadata block for a forwarded WhatsApp message."""
    sender = evt.push_name or evt.sender_phone or "(未知)"
    lines = [
        f"**来自**：{sender}",
        f"**号码**：`{evt.sender_phone}`",
        f"**实例**：{evt.instance}",
        f"**时间**：{_ts()}",
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)


def _base_card() -> dict:
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": FEISHU_CARD_TEMPLATE,
            "title": {"tag": "plain_text", "content": FEISHU_CARD_TITLE},
        },
        "elements": [],
    }
    if FEISHU_CARD_FOOTER:
        card["elements"].append(
            {"tag": "note", "elements": [{"tag": "plain_text", "content": FEISHU_CARD_FOOTER}]}
        )
    return card


def text_card(evt: EvolutionMessage) -> dict:
    card = _base_card()
    card["elements"].insert(0, {"tag": "markdown", "content": _meta_markdown(evt) + "\n\n" + evt.text})
    return card


def image_card(evt: EvolutionMessage, img_key: str) -> dict:
    card = _base_card()
    card["elements"].insert(
        0,
        {
            "tag": "img",
            "img_key": img_key,
            "alt": {"tag": "plain_text", "content": "WhatsApp 图片"},
        },
    )
    card["elements"].insert(1, {"tag": "markdown", "content": _meta_markdown(evt, note="**[图片消息]**")})
    return card


def file_card(evt: EvolutionMessage, file_key: str, file_name: str) -> dict:
    card = _base_card()
    card["elements"].insert(
        0,
        {
            "tag": "file",
            "file_key": file_key,
            "name": file_name,
        },
    )
    card["elements"].insert(
        1, {"tag": "markdown", "content": _meta_markdown(evt, note=f"**[文件消息]** {file_name}")}
    )
    return card


def placeholder_card(evt: EvolutionMessage, note: str) -> dict:
    card = _base_card()
    card["elements"].insert(0, {"tag": "markdown", "content": _meta_markdown(evt, note=note)})
    return card


def confirm_card(customer: str, reply_preview: str) -> dict:
    """Small confirmation card shown in the group after a Feishu -> WA reply."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "已回复客户"},
        },
        "elements": [
            {"tag": "markdown", "content": f"✅ 已通过 WhatsApp 回复客户 **{customer}**：\n\n{reply_preview}"},
        ],
    }


def error_card(reason: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "回复失败"},
        },
        "elements": [
            {"tag": "markdown", "content": f"❌ 回复未能发送：`{reason}`"},
        ],
    }


def codex_card(content: str, state: str = "working") -> dict:
    """Build the editable status/result card used by the marketing Codex bridge."""
    styles = {
        "working": ("blue", "Codex 正在工作"),
        "uploading": ("orange", "Codex 正在交付文件"),
        "done": ("green", "Codex 已完成"),
        "error": ("red", "Codex 执行失败"),
    }
    template, title = styles.get(state, styles["working"])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [{"tag": "markdown", "content": content}],
    }

"""Feishu interactive card builders for forwarded WhatsApp messages."""

from datetime import datetime, timedelta, timezone

from .evolution import EvolutionMessage

SHANGHAI_TZ = timezone(timedelta(hours=8))

HEADER = "WhatsApp 新消息"


def _ts() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _meta_markdown(evt: EvolutionMessage, note: str = "") -> str:
    """Metadata block; keeps WA_NUMBER/INSTANCE lines for future Feishu->WA replies."""
    sender = evt.push_name or evt.sender_phone or "(未知)"
    lines = [
        f"**来自**：{sender}",
        f"**号码**：`{evt.sender_phone}`",
        f"**实例**：{evt.instance}",
        f"**时间**：{_ts()}",
    ]
    if note:
        lines.append(note)
    lines += [
        "",
        f"WA_NUMBER:{evt.sender_phone}",
        f"INSTANCE:{evt.instance}",
    ]
    return "\n".join(lines)


def _base_card() -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": HEADER},
        },
        "elements": [],
    }


def text_card(evt: EvolutionMessage) -> dict:
    card = _base_card()
    card["elements"] = [
        {"tag": "markdown", "content": _meta_markdown(evt) + "\n\n" + evt.text},
    ]
    return card


def image_card(evt: EvolutionMessage, img_key: str) -> dict:
    card = _base_card()
    card["elements"] = [
        {
            "tag": "img",
            "img_key": img_key,
            "alt": {"tag": "plain_text", "content": "WhatsApp 图片"},
        },
        {"tag": "markdown", "content": _meta_markdown(evt, note="**[图片消息]**")},
    ]
    return card


def file_card(evt: EvolutionMessage, file_key: str, file_name: str) -> dict:
    card = _base_card()
    card["elements"] = [
        {
            "tag": "file",
            "file_key": file_key,
            "name": file_name,
        },
        {"tag": "markdown", "content": _meta_markdown(evt, note=f"**[文件消息]** {file_name}")},
    ]
    return card


def placeholder_card(evt: EvolutionMessage, note: str) -> dict:
    card = _base_card()
    card["elements"] = [
        {"tag": "markdown", "content": _meta_markdown(evt, note=note)},
    ]
    return card

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple Music 上架监控机器人（全中文交互版）

支持的 Telegram 指令（直接发给你的 bot）：
  野人 孟维来           -> 添加监听（默认大陆区 cn）
  野人 孟维来 us         -> 添加监听，指定国区（us/cn/hk/tw/jp/kr/gb/sg...）
  /list                -> 查看当前监听清单
  /del 3               -> 按 /list 显示的编号删除一条监听
  /help                -> 查看使用说明
"""

import os
import re
import json
import requests
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

SONGS_FILE = "songs.json"
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "你的BotToken")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "你的ChatID")

KNOWN_COUNTRIES = {
    "cn", "us", "hk", "tw", "jp", "kr", "gb", "sg", "ca", "au", "de", "fr",
}

DEFAULT_TEMPLATE = "🎉 上架啦！\n《{歌名}》- {歌手}"

HELP_TEXT = (
    "🎵 使用说明\n\n"
    "添加监听（直接发歌名和歌手，中间空格隔开）：\n"
    "野人 孟维来\n\n"
    "指定国区（默认大陆区 cn）：\n"
    "野人 孟维来 us\n\n"
    "查看当前监听清单：\n"
    "/list\n\n"
    "删除某条监听（编号看 /list）：\n"
    "/del 3\n\n"
    "查看/修改通知格式：\n"
    "/template\n\n"
    "重新查看本说明：\n"
    "/help"
)

TEMPLATE_HELP = (
    "📝 当前通知格式：\n{current}\n\n"
    "可以用的占位符：{{歌名}} {{歌手}}\n"
    "换行请用 \\n 表示。\n\n"
    "改成新格式，例如：\n"
    "/template 新歌上线~\\n{{歌名}} by {{歌手}}\n\n"
    "恢复默认格式：\n"
    "/template 重置"
)


def load_songs():
    if not os.path.exists(SONGS_FILE):
        return []
    with open(SONGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_songs(songs):
    with open(SONGS_FILE, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"template": DEFAULT_TEMPLATE}


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"_telegram_offset": 0, "songs": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def song_key(song, artist, country):
    return f"{country}:{artist}:{song}"


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram(text, html=False):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text}
    if html:
        payload["parse_mode"] = "HTML"
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


def notify_found(title, artist, url, template):
    """通知上架，但不显示裸链接，只留下方的预览卡片"""
    body = template.replace("{歌名}", title).replace("{歌手}", artist)
    body_e = escape_html(body)
    # 用一个不可见字符做超链接的"显示文字"，既能触发 Telegram 的链接预览卡片，
    # 又不会在正文里出现一大串丑陋的网址。
    text = f"{body_e}\n<a href=\"{url}\">&#8203;</a>"
    send_telegram(text, html=True)


def parse_incoming_text(text, songs, config):
    """
    解析一条 Telegram 消息，返回 (reply_text, songs_changed, config_changed)
    支持：/list  /del N  /help  /template  以及"歌名 歌手 [国区]"这种自然输入
    """
    text = text.strip()

    if text in ("/start", "/help", "帮助", "怎么用"):
        return HELP_TEXT, False, False

    if text == "/list":
        if not songs:
            return "目前还没有监听任何歌曲，直接发「歌名 歌手」就能添加。", False, False
        lines = ["📋 当前监听清单：\n"]
        for i, s in enumerate(songs, 1):
            country = s.get("country", "cn")
            lines.append(f"{i}. 《{s['song']}》- {s['artist']}（{country}区）")
        return "\n".join(lines), False, False

    m = re.match(r"^/del\s+(\d+)$", text)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(songs):
            removed = songs.pop(idx)
            return f"🗑️ 已取消监听：《{removed['song']}》- {removed['artist']}", True, False
        return "编号不对，先发 /list 看看当前编号。", False, False

    if text == "/template":
        current = config.get("template", DEFAULT_TEMPLATE)
        return TEMPLATE_HELP.format(current=current), False, False

    if text.startswith("/template "):
        new_template_raw = text[len("/template "):].strip()
        if new_template_raw in ("重置", "reset"):
            config["template"] = DEFAULT_TEMPLATE
            return f"✅ 已恢复默认格式：\n{DEFAULT_TEMPLATE}", False, True
        new_template = new_template_raw.replace("\\n", "\n")
        if "{歌名}" not in new_template and "{歌手}" not in new_template:
            return "格式里至少要包含 {歌名} 或 {歌手} 其中一个占位符，不然通知会看不出是哪首歌。", False, False
        config["template"] = new_template
        preview = new_template.replace("{歌名}", "野人").replace("{歌手}", "孟维来")
        return f"✅ 通知格式已更新，效果预览：\n\n{preview}", False, True

    if text.startswith("/"):
        return "指令不认识，发 /help 看看支持哪些用法。", False, False

    # 自然语言添加：野人 孟维来 [国区]
    tokens = text.split()
    if len(tokens) < 2:
        return "格式不太对，至少要「歌名 歌手」两部分，中间空格隔开。发 /help 看例子。", False, False

    country = "cn"
    if tokens[-1].lower() in KNOWN_COUNTRIES and len(tokens) >= 3:
        country = tokens[-1].lower()
        tokens = tokens[:-1]

    song = tokens[0]
    artist = " ".join(tokens[1:])

    if not song or not artist:
        return "格式不太对，至少要「歌名 歌手」两部分，中间空格隔开。发 /help 看例子。", False, False

    key = song_key(song, artist, country)
    if any(song_key(s["song"], s["artist"], s.get("country", "cn")) == key for s in songs):
        return f"这首已经在监听清单里了：《{song}》- {artist}（{country}区）", False, False

    songs.append({"song": song, "artist": artist, "country": country})
    return f"✅ 已加入监听：《{song}》- {artist}（{country}区）\n上架后第一时间通知你。", True, False


def poll_telegram(songs, state, config):
    offset = state.get("_telegram_offset", 0)
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates"
    resp = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=15)
    resp.raise_for_status()
    updates = resp.json().get("result", [])

    songs_changed = False
    config_changed = False
    for upd in updates:
        state["_telegram_offset"] = upd["update_id"] + 1
        msg = upd.get("message", {})
        text = msg.get("text", "")
        if not text:
            continue

        reply, s_changed, c_changed = parse_incoming_text(text, songs, config)
        send_telegram(reply)
        if s_changed:
            songs_changed = True
            print(f"[歌单变更] 处理消息：{text!r}")
        if c_changed:
            config_changed = True
            print(f"[格式变更] 处理消息：{text!r}")

    return songs_changed, config_changed


def search_apple_music(song, artist, country):
    term = quote_plus(f"{artist} {song}")
    url = f"https://music.apple.com/{country}/search?term={term}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", {"id": "serialized-server-data"})
    if not script_tag:
        return []

    data = json.loads(script_tag.text)

    sections = None
    try:
        if isinstance(data, dict) and "data" in data:
            inner = data["data"]
            if isinstance(inner, list):
                sections = inner[0]["data"]["sections"]
            elif isinstance(inner, dict):
                sections = inner["sections"]
        elif isinstance(data, list):
            sections = data[0]["data"]["sections"]
    except Exception:
        return []

    if not sections:
        return []

    songs_section = None
    for sec in sections:
        if "song" in sec.get("id", ""):
            songs_section = sec
            break
    if not songs_section:
        return []

    results = []
    for item in songs_section.get("items", []):
        try:
            title = item.get("title", "")
            artist_name = item.get("subtitleLinks", [{}])[0].get("title", "")
            link = item.get("contentDescriptor", {}).get("url", "")
            results.append({"title": title, "artist": artist_name, "url": link})
        except Exception:
            continue
    return results


def is_match(item, song, artist):
    return song in item["title"] and artist in item["artist"]


def check_all_songs(songs, state, config):
    found_state = state.setdefault("songs", {})
    for entry in songs:
        song = entry["song"]
        artist = entry["artist"]
        country = entry.get("country", "cn")
        key = song_key(song, artist, country)

        if found_state.get(key, {}).get("found"):
            print(f"[跳过] 已通知过：{artist} - {song}（{country}）")
            continue

        print(f"[检查] {artist} - {song}（{country}）...")
        try:
            results = search_apple_music(song, artist, country)
        except Exception as e:
            print(f"  查询出错：{e}")
            continue

        match = next((r for r in results if is_match(r, song, artist)), None)

        if match:
            template = config.get("template", DEFAULT_TEMPLATE)
            notify_found(match["title"], match["artist"], match["url"], template)
            print(f"  已发送通知：{match['title']} - {match['artist']}")
            found_state[key] = {"found": True}
        else:
            print("  暂未上架")


def main():
    songs = load_songs()
    state = load_state()
    config = load_config()

    songs_changed, config_changed = poll_telegram(songs, state, config)
    if songs_changed:
        save_songs(songs)
    if config_changed:
        save_config(config)

    check_all_songs(songs, state, config)
    save_state(state)


if __name__ == "__main__":
    main()

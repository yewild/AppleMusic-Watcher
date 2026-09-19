#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple Music 上架检查脚本 —— GitHub Actions 版
只负责：读取 songs.json/config.json，查是否上架，上架了发 Telegram 通知。
不负责：处理 Telegram 聊天指令（那部分现在由 Cloudflare Worker 秒回处理）。
"""

import os
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

DEFAULT_TEMPLATE = "🎉 上架啦！\n《{歌名}》- {歌手}"


def load_songs():
    if not os.path.exists(SONGS_FILE):
        return []
    with open(SONGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"template": DEFAULT_TEMPLATE}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"songs": {}}


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
    body = template.replace("{歌名}", title).replace("{歌手}", artist)
    body_e = escape_html(body)
    text = f"{body_e}\n<a href=\"{url}\">&#8203;</a>"
    send_telegram(text, html=True)


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


def main():
    songs = load_songs()
    config = load_config()
    state = load_state()
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

    save_state(state)


if __name__ == "__main__":
    main()

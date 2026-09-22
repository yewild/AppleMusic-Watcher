#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple Music 上架检查脚本 —— GitHub Actions 版
只负责：读取 songs.json，查是否上架，上架了发 Telegram 通知（专辑封面图 + 歌名 + 歌手）。
不负责：处理 Telegram 聊天指令（那部分现在由 Cloudflare Worker 秒回处理）。
"""

import os
import json
import requests
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import opencc

import re

_converter = opencc.OpenCC("t2s")  # 统一转成简体再比较，不管输入是繁体还是简体


def normalize(text):
    text = _converter.convert(text)
    text = text.lower()  # 英文不区分大小写
    text = re.sub(r"\s+", "", text)  # 去掉所有空格，避免排版差异导致比对失败
    # 全角符号统一转成半角，避免"（）"和"()"这种视觉一样但编码不同的符号导致误判
    fullwidth = "（）－·＆／！？：；，。"
    halfwidth = "()-·&/!?:;,."
    text = text.translate(str.maketrans(fullwidth, halfwidth))
    return text

SONGS_FILE = "songs.json"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "你的BotToken")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "你的ChatID")


def load_songs():
    if not os.path.exists(SONGS_FILE):
        return []
    with open(SONGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_songs(songs):
    with open(SONGS_FILE, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram(text, html=False, disable_preview=True):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if html:
        payload["parse_mode"] = "HTML"
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


def notify_found(title, artist, url, artwork_url=""):
    """单行"歌名 - 歌手"，链接藏起来只用来触发下面的小卡片，
    不再显示"查看详情"这种多余文字（点卡片本身就能跳转）"""
    title_e = escape_html(title)
    artist_e = escape_html(artist)
    text = f'{title_e} - {artist_e}<a href="{url}">&#8203;</a>'
    send_telegram(text, html=True, disable_preview=False)


def send_telegram_photo(photo_url, caption_html):
    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    resp = requests.post(api_url, json={
        "chat_id": TG_CHAT_ID,
        "photo": photo_url,
        "caption": caption_html,
        "parse_mode": "HTML",
    }, timeout=15)
    resp.raise_for_status()


def search_apple_music(song, artist, country, debug_song=None):
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
            artwork = extract_artwork_url(item)

            # 调试：只在这条结果看起来像是我们要找的目标时，才打印完整原始字段，避免刷屏
            if debug_song and normalize(debug_song) in normalize(title) and not artwork:
                print(f"    [调试] 没抠到封面图，这条结果的完整原始字段如下：")
                print(json.dumps(item, ensure_ascii=False)[:3000])

            results.append({"title": title, "artist": artist_name, "url": link, "artwork": artwork})
        except Exception:
            continue
    return results


def extract_artwork_url(item):
    """拿专辑封面图。真实结构是 item.artwork.dictionary.url（比想象中多套了一层）"""
    try:
        artwork = item.get("artwork") or {}
        info = artwork.get("dictionary") or {}
        url = info.get("url") or ""
        if not url:
            return ""
        url = url.replace("{w}", "400").replace("{h}", "400")
        url = url.replace("{c}", "bb").replace("{f}", "jpg")
        return url
    except Exception:
        return ""


EXCLUDE_KEYWORDS = [
    "live", "伴奏", "remix", "instrumental", "karaoke", "demo",
    "精简版", "现场", "音乐会版", "live version", "unplugged",
    "concert", "演唱会", "伴唱", "纯音乐",
]


def is_excluded(title):
    """过滤掉Live/伴奏/Remix这类非正式版本，避免历史演出录音被误判成"新歌上架\""""
    title_n = normalize(title)
    return any(normalize(kw) in title_n for kw in EXCLUDE_KEYWORDS)


def is_match(item, song, artist):
    if is_excluded(item["title"]):
        return False

    song_n = normalize(song)
    title_n = normalize(item["title"])

    if len(song_n) <= 1:
        # 歌名只有一个字时，用"完全相等"代替"包含"，避免大量误报
        song_ok = song_n == title_n
    else:
        song_ok = song_n in title_n

    if not song_ok:
        return False

    # 歌手比对放宽：把你输入的歌手名按常见分隔符拆开，
    # 只要有一个能在苹果返回的歌手栏位里找到，就算匹配上
    # （应付"合唱"这种苹果实际显示成 A & B / A、B / A x B 的情况）
    parts = [p.strip() for p in re.split(r"[,，、&/xX×]| feat\.?| ft\.?", artist) if p.strip()]
    if not parts:
        parts = [artist]

    combined_artist = normalize(item["artist"])
    return any(normalize(p) in combined_artist for p in parts)


def main():
    songs = load_songs()
    songs_changed = False

    for entry in songs:
        song = entry["song"]
        artist = entry["artist"]
        country = entry.get("country", "cn")

        if entry.get("notified"):
            print(f"[跳过] 已通知过：{artist} - {song}（{country}）")
            continue

        print(f"[检查] {artist} - {song}（{country}）...")
        try:
            results = search_apple_music(song, artist, country, debug_song=song)
        except Exception as e:
            print(f"  查询出错：{e}")
            continue

        match = next((r for r in results if is_match(r, song, artist)), None)

        if match:
            notify_found(match["title"], match["artist"], match["url"])
            print(f"  已发送通知：{match['title']} - {match['artist']}")
            entry["notified"] = True
            entry["matched_title"] = match["title"]
            songs_changed = True
        else:
            print("  暂未上架")

    if songs_changed:
        save_songs(songs)
        print("songs.json 有更新，交给workflow提交")


if __name__ == "__main__":
    main()

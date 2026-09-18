#!/usr/bin/env python3
"""
监控一首歌是否在 Apple Music 指定国区上架，上架后通过 Telegram 通知。
原理：直接请求 Apple Music 网页版搜索页，解析页面里内嵌的
serialized-server-data 结构化数据（网页自己渲染用的原始数据），
不需要额外去偷任何 token，比之前的方案更稳。
"""

import os
import json
import requests
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

CONFIG = {
    "SONG_NAME": "野人",
    "ARTIST_NAME": "孟維來",
    "COUNTRY": "cn",
    "TG_BOT_TOKEN": "你的BotToken",
    "TG_CHAT_ID": "你的ChatID",
}

STATE_FILE = "state.json"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"


def get_config(key):
    return os.environ.get(key, CONFIG[key])


def search_apple_music(song, artist, country):
    term = quote_plus(f"{artist} {song}")
    url = f"https://music.apple.com/{country}/search?term={term}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    print("调试信息：页面状态码：", resp.status_code)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # 强制按 utf-8 解码，避免中文乱码

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", {"id": "serialized-server-data"})
    if not script_tag:
        print("调试信息：页面里没找到 serialized-server-data，页面片段：")
        print(resp.text[:500])
        return []

    data = json.loads(script_tag.text)

    print("调试信息：data 顶层类型：", type(data))
    if isinstance(data, dict):
        print("调试信息：data 顶层 keys：", list(data.keys())[:20])
    elif isinstance(data, list):
        print("调试信息：data 是列表，长度：", len(data))
        if data:
            print("调试信息：data[0] 类型：", type(data[0]))
            if isinstance(data[0], dict):
                print("调试信息：data[0] keys：", list(data[0].keys())[:20])

    # 真实结构：{"data": [ {"data": {"sections": [...]}} ], "userTokenHash": ...}
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
    except Exception as e:
        print("调试信息：按常规路径取 sections 失败：", e)

    if sections is None:
        print("调试信息：没能定位到 sections，打印原始 JSON 前 1500 字符：")
        print(json.dumps(data, ensure_ascii=False)[:1500])
        return []

    print("调试信息：sections 数量：", len(sections))
    for sec in sections[:10]:
        print("  section id：", sec.get("id"))

    songs_section = None
    for sec in sections:
        if "song" in sec.get("id", ""):
            songs_section = sec
            break

    if not songs_section:
        print("调试信息：结果里没有歌曲区块（可能真的没搜到任何东西）")
        return []

    results = []
    for item in songs_section.get("items", []):
        try:
            title = item.get("title", "")
            artist_name = item.get("subtitleLinks", [{}])[0].get("title", "")
            link = item.get("contentDescriptor", {}).get("url", "")
            results.append({"title": title, "artist": artist_name, "url": link})
        except Exception as e:
            print("调试信息：解析某条结果出错，跳过：", e)
    return results


def is_match(item, song, artist):
    return song in item["title"] and artist in item["artist"]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"found": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
    resp.raise_for_status()


def main():
    song = get_config("SONG_NAME")
    artist = get_config("ARTIST_NAME")
    country = get_config("COUNTRY")
    tg_token = get_config("TG_BOT_TOKEN")
    tg_chat_id = get_config("TG_CHAT_ID")

    state = load_state()
    if state.get("found"):
        print("已经通知过了，跳过本次检查。")
        return

    results = search_apple_music(song, artist, country)
    print(f"调试信息：搜到 {len(results)} 条歌曲结果")
    for r in results[:5]:
        print("  -", r["title"], "/", r["artist"])

    match = next((r for r in results if is_match(r, song, artist)), None)

    if match:
        text = f"🎉 上架啦！\n《{match['title']}》- {match['artist']}\n{match['url']}"
        send_telegram(tg_token, tg_chat_id, text)
        print("已发送 Telegram 通知：", text)
        save_state({"found": True})
    else:
        print(f"暂未上架（或未匹配到）：{artist} - {song}（{country} 区）")


if __name__ == "__main__":
    main()

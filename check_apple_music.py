#!/usr/bin/env python3
"""
监控一首歌是否在 Apple Music 上架，上架后通过 Telegram 通知。
配置方式：把下面 CONFIG 里的内容改掉，或者用同名的环境变量覆盖（推荐用于 GitHub Actions）。
"""

import os
import json
import requests

# ========== 在这里填你的信息（或用环境变量覆盖） ==========
CONFIG = {
    "SONG_NAME": "歌名",
    "ARTIST_NAME": "歌手名",
    "COUNTRY": "us",
    "TG_BOT_TOKEN": "你的BotToken",
    "TG_CHAT_ID": "你的ChatID",
}
# ==========================================================

STATE_FILE = "state.json"


def get_config(key):
    return os.environ.get(key, CONFIG[key])


def search_itunes(song, artist, country):
    term = f"{artist} {song}"
    url = "https://itunes.apple.com/search"
    params = {
        "term": term,
        "entity": "song",
        "country": country,
        "limit": 10,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def is_match(result, song, artist):
    track = result.get("trackName", "").lower()
    art = result.get("artistName", "").lower()
    return song.lower() in track and artist.lower() in art


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

    results = search_itunes(song, artist, country)
    match = next((r for r in results if is_match(r, song, artist)), None)

    if match:
        link = match.get("trackViewUrl", "")
        text = (
            f"🎉 上架啦！\n"
            f"《{match.get('trackName')}》- {match.get('artistName')}\n"
            f"{link}"
        )
        send_telegram(tg_token, tg_chat_id, text)
        print("已发送 Telegram 通知：", text)
        save_state({"found": True})
    else:
        print(f"暂未上架：{artist} - {song}（{country} 区）")


if __name__ == "__main__":
    main()

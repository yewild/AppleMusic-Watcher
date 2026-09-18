#!/usr/bin/env python3
"""
监控一首歌是否在 Apple Music 指定国区上架，上架后通过 Telegram 通知。
用的是 Apple Music 网页版背后的接口（比老旧的 iTunes Search API 靠谱，
尤其是大陆区，旧接口的 country=cn 基本是坏的）。
"""

import os
import re
import json
import requests

CONFIG = {
    "SONG_NAME": "野人",
    "ARTIST_NAME": "孟維來",   # 繁体，之前验证过大陆区库里是繁体
    "COUNTRY": "cn",
    "TG_BOT_TOKEN": "你的BotToken",
    "TG_CHAT_ID": "你的ChatID",
}

STATE_FILE = "state.json"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"


def get_config(key):
    return os.environ.get(key, CONFIG[key])


def get_apple_music_token():
    """从 Apple Music 网页版首页里，扒出苹果自己前端用的公开访问令牌"""
    resp = requests.get(
        "https://music.apple.com/us/search",
        headers={"User-Agent": UA},
        timeout=20,
    )
    resp.raise_for_status()
    html = resp.text

    # 方式一：直接找 "token":"xxxx"
    m = re.search(r'"token":"([^"]+)"', html)
    if m:
        return m.group(1)

    # 方式二：找 meta 标签里的环境配置 JSON
    m = re.search(
        r'name="desktop-music-app/config/environment"\s+content="([^"]+)"', html
    )
    if m:
        import urllib.parse

        decoded = urllib.parse.unquote(m.group(1))
        data = json.loads(decoded)
        return data["MEDIA_API"]["token"]

    print("调试信息：没能从页面里找到 token，页面片段如下：")
    print(html[:500])
    raise RuntimeError("拿不到 Apple Music token")


def search_apple_music(token, song, artist, country):
    url = f"https://amp-api.music.apple.com/v1/catalog/{country}/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "https://music.apple.com",
        "User-Agent": UA,
    }
    params = {"term": f"{artist} {song}", "types": "songs", "limit": 10}
    resp = requests.get(url, headers=headers, params=params, timeout=20)
    print("调试信息：接口状态码：", resp.status_code)
    if resp.status_code != 200:
        print("调试信息：返回内容：", resp.text[:500])
        resp.raise_for_status()
    data = resp.json()
    return data.get("results", {}).get("songs", {}).get("data", [])


def is_match(item, song, artist):
    attrs = item.get("attributes", {})
    name = attrs.get("name", "")
    art = attrs.get("artistName", "")
    return song in name and artist in art


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

    token = get_apple_music_token()
    print("调试信息：拿到 token，长度：", len(token))

    results = search_apple_music(token, song, artist, country)
    print(f"调试信息：搜到 {len(results)} 条结果")
    for r in results[:5]:
        a = r.get("attributes", {})
        print("  -", a.get("name"), "/", a.get("artistName"))

    match = next((r for r in results if is_match(r, song, artist)), None)

    if match:
        attrs = match["attributes"]
        link = attrs.get("url", "")
        text = f"🎉 上架啦！\n《{attrs.get('name')}》- {attrs.get('artistName')}\n{link}"
        send_telegram(tg_token, tg_chat_id, text)
        print("已发送 Telegram 通知：", text)
        save_state({"found": True})
    else:
        print(f"暂未上架（或未匹配到）：{artist} - {song}（{country} 区）")


if __name__ == "__main__":
    main()

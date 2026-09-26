import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
CAPTURES = ROOT / "captures"
CAPTURES.mkdir(exist_ok=True)
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
USER_ID = os.environ.get("DISCORD_USER_ID", "")
PREVIOUSLY_FOUND = os.environ.get("IGNITE_FOUND_BEFORE", "false").lower() == "true"
SITES = [
    ("itunestore", "https://www.itunestore.com/it/music/topsongs/27-j-pop/"),
    ("itopchart", "https://itopchart.com/it/ja/top-songs/j-pop/"),
]


def is_target(title, artist):
    return bool(re.search(r"\bIGNITE\b", title, re.I) and re.search(r"\bJO1\b", artist, re.I))


def send_discord(message, images=()):
    if not WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK_URL secret is missing")
    mention = f"<@{USER_ID}> " if USER_ID else ""
    response = requests.post(
        WEBHOOK,
        json={"content": mention + message, "allowed_mentions": {"users": [USER_ID] if USER_ID else []}},
        timeout=30,
    )
    response.raise_for_status()
    for image in images:
        with image.open("rb") as handle:
            response = requests.post(WEBHOOK, files={"files[0]": (image.name, handle, "image/png")}, timeout=60)
        response.raise_for_status()


def main():
    stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d_%H%M%S_JST")
    images = []
    matches = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
        for site, start_url in SITES:
            url = start_url
            seen = set()
            for part in range(1, 11):
                if url in seen:
                    break
                seen.add(url)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.locator("body").wait_for(timeout=15000)
                    if site == "itopchart":
                        rows = page.locator(".item_box").evaluate_all("els => els.map(e => ({title: e.querySelector('.item_name')?.textContent?.trim() || '', artist: e.querySelector('.artist')?.textContent?.trim() || '', rank: e.querySelector('.rank li')?.textContent?.trim() || ''}))")
                        if not rows:
                            raise RuntimeError("iTopChart rank rows were not found")
                        matches.extend({"site": site, **r} for r in rows if is_target(r["title"], r["artist"]))
                    else:
                        titles = page.locator(".thumbnail h4.text-overflow").all_text_contents()
                        if not titles:
                            raise RuntimeError("iTunesStore song rows were not found")
                        for title in titles:
                            song, _, artist = title.rpartition(" - ")
                            if is_target(song, artist):
                                matches.append({"site": site, "title": song, "artist": artist, "rank": f"page {part}"})
                    image = CAPTURES / f"{stamp}_{site}_{part}.png"
                    page.screenshot(path=str(image), full_page=True, animations="disabled", timeout=90000)
                    images.append(image)
                    if site == "itopchart":
                        break
                    next_links = page.locator("a").filter(has_text=re.compile(r"^Next\s*»$")).all()
                    url = next_links[-1].get_attribute("href") if next_links else None
                    if not url:
                        break
                    from urllib.parse import urljoin
                    url = urljoin(page.url, url)
                except Exception as exc:
                    errors.append(f"{site} page {part}: {exc}")
                    break
        browser.close()

    if errors:
        print("Warnings: " + "; ".join(errors))
    if not images or any(not image.exists() for image in images):
        raise RuntimeError("No valid screenshots saved: " + "; ".join(errors))
    (CAPTURES / "result.json").write_text(json.dumps({"stamp": stamp, "matches": matches, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    found_now = bool(matches)
    first_detection = found_now and not PREVIOUSLY_FOUND
    if first_detection:
        (ROOT / "ignite-found.flag").write_text(stamp, encoding="utf-8")
        details = "、".join(f"{m['site']} {m['rank']}" for m in matches)
        send_discord(f"🚨 JO1『IGNITE』をイタリアのJ-Popランキングで検出しました！ {details}\n撮影: {stamp}", images)
    elif PREVIOUSLY_FOUND:
        send_discord(f"📸 イタリアJ-Popランキング定点撮影: {stamp}", images)
    print(json.dumps({"stamp": stamp, "images": len(images), "matches": matches, "first_detection": first_detection}, ensure_ascii=False))


if __name__ == "__main__":
    main()

import os, json, time, asyncio, requests
from pathlib import Path
from typing import List, Dict, Any, Tuple
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

URL = os.getenv("TARGET_URL", "http://cheeks.nagoya/yoyaku.shtml")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
STATE_PATH = Path("state.json")

FEMALE_MIN = int(os.getenv("FEMALE_MIN", "3"))
FEMALE_RATIO_MIN = float(os.getenv("FEMALE_RATIO_MIN", "0.3"))

def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"etag": None, "last_modified": None, "days": {}}

def save_state(state: Dict[str, Any]):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)

def notify_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        print("[WARN] SLACK_WEBHOOK_URL not set. Message:\n", text)
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    except Exception as e:
        print("[ERROR] Slack notify failed:", e)

def should_skip_by_http_headers(prev: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    try:
        r = requests.head(URL, timeout=10)
        etag = r.headers.get("ETag")
        lm = r.headers.get("Last-Modified")
        same = (etag and etag == prev.get("etag")) or (lm and lm == prev.get("last_modified"))
        return same, {"etag": etag, "last_modified": lm}
    except Exception:
        return False, {"etag": None, "last_modified": None}

async def fetch_calendar_html() -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36"
        ))
        page = await context.new_page()
        try:
            await page.goto(URL, wait_until="networkidle", timeout=60000)
            html = await page.content()
            return html
        finally:
            await context.close()
            await browser.close()

def parse_day_entries(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table[border='2']")
    if not table:
        return []
    results = []
    for td in table.select("td[valign='top']"):
        centers = td.find_all("center")
        if not centers:
            continue
        day_text = (centers[0].get_text(strip=True) if len(centers) >= 1 else "")
        if not day_text.isdigit():
            continue
        day = int(day_text)
        people_center = centers[2] if len(centers) >= 3 else (centers[1] if len(centers) >= 2 else None)
        male = female = 0
        if people_center:
            for f in people_center.select("font"):
                t = f.get_text(strip=True)
                male += t.count("♂")
                female += t.count("♀")
        total = male + female
        ratio = (female/total) if total>0 else 0.0
        results.append({"day": day, "male": male, "female": female, "total": total, "ratio": round(ratio,3),
                        "meets": (female >= FEMALE_MIN) and (ratio >= FEMALE_RATIO_MIN)})
    results.sort(key=lambda x: x["day"])
    return results

def diff_changes(prev_days: Dict[str, Any], curr_stats: List[Dict[str, Any]]):
    changed, newly_meets = [], []
    for s in curr_stats:
        key = str(s["day"])
        prev = prev_days.get(key)
        if not prev:
            if s["total"] > 0:
                changed.append(s)
            if s.get("meets"):
                newly_meets.append(s)
            continue
        if (s["male"], s["female"], s["total"]) != (prev.get("male"), prev.get("female"), prev.get("total")):
            changed.append(s)
        if (not prev.get("meets")) and s.get("meets"):
            newly_meets.append(s)
    return changed, newly_meets

async def run():
    state = load_state()
    skip, hdrs = should_skip_by_http_headers(state)
    if skip:
        print("[INFO] Skip by ETag/Last-Modified.")
        return 0

    # fetch with retries
    delay = 1
    for attempt in range(3):
        try:
            html = await fetch_calendar_html()
            break
        except Exception as e:
            if attempt == 2:
                notify_slack(f"[ERROR] fetch failed: {e}")
                return 1
            time.sleep(delay); delay = delay * 2 + 1

    stats = parse_day_entries(html)
    changed, newly_meets = diff_changes(state.get("days", {}), stats)

    new_days = {str(s["day"]): {"male": s["male"], "female": s["female"], "total": s["total"],
                                "ratio": s["ratio"], "meets": s["meets"]} for s in stats}
    state.update({"etag": hdrs["etag"], "last_modified": hdrs["last_modified"], "days": new_days})
    save_state(state)

    if changed or newly_meets:
        lines = []
        if newly_meets:
            lines.append("【新規で条件を満たした日】")
            for s in newly_meets:
                lines.append(f"- {s['day']}日: 女{s['female']}/全{s['total']} ({int(s['ratio']*100)}%)")
        if changed:
            lines.append("【人数が更新された日】")
            for s in changed:
                lines.append(f"- {s['day']}日: 男{s['male']} 女{s['female']} 全{s['total']} ({int(s['ratio']*100)}%)")
        lines.append(f"URL: {URL}")
        notify_slack("\n".join(lines))
    else:
        print("[INFO] no change.")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Tuple, Optional, List

import requests
from bs4 import BeautifulSoup


USER_AGENT = "Mozilla/5.0 (compatible; ElbphiTicketWatch/1.0)"
TIMEOUT = 20
STATE_FILE = Path("ticket_state.json")


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def extract_title_and_datetime(text: str) -> Tuple[str, str]:
    title = ""
    dt = ""
    m = re.search(
        r"\b(?:Mo|Di|Mi|Do|Fr|Sa|So),\s*\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}(?::\d{2})?\s*Uhr\b",
        text,
    )
    if m:
        dt = m.group(0)
    return title, dt


def detect_state(html: str) -> Tuple[str, Dict[str, str]]:
    """
    state: SOLD_OUT | AVAILABLE | NOT_ON_SALE | UNKNOWN
    """
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    page_title = h1.get_text(strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")

    text = soup.get_text(" ", strip=True)
    _, dt = extract_title_and_datetime(text)

    # wichtig: SOLD_OUT zuerst prüfen (auf Elbphi-Seiten kann trotzdem "Tickets ab" stehen)
    if "Ausverkauft" in text:
        return "SOLD_OUT", {"title": page_title, "datetime": dt}
# Nur "Vorverkauf" zählen, wenn wirklich ein Starttermin genannt wird (nicht der Merkliste-Footer!)
    if re.search(r"\bTicketvorverkauf\s+ab\s+\d{1,2}\.\d{1,2}\.\d{4}\b", text) or \
       re.search(r"\bVorverkauf\s+ab\s+\d{1,2}\.\d{1,2}\.\d{4}\b", text):
        return "NOT_ON_SALE", {"title": page_title, "datetime": dt}


    if "Tickets ab" in text or "Tickets kaufen" in text:
        return "AVAILABLE", {"title": page_title, "datetime": dt}

    return "UNKNOWN", {"title": page_title, "datetime": dt}


def load_state() -> Dict[str, dict]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: Dict[str, dict]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ntfy_notify(message: str, title: str = "Elbphilharmonie Tickets") -> None:
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    server = (os.environ.get("NTFY_URL") or "https://ntfy.sh").strip()
    if not topic:
        # Kein Push konfiguriert – einfach nur loggen
        print("[INFO] NTFY_TOPIC nicht gesetzt, keine Push-Nachricht gesendet.")
        return

    url = f"{server.rstrip('/')}/{topic}"
    requests.post(
        url,
        data=message.encode("utf-8"),
        headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
        timeout=TIMEOUT,
    ).raise_for_status()


def parse_urls(env_value: str) -> List[str]:
    urls = []
    for line in env_value.splitlines():
        line = line.strip()
        if not line:
            continue
        urls.append(line)
    # fallback: auch whitespace-getrennt
    if not urls:
        urls = [u for u in env_value.split() if u.strip()]
    return urls


def main() -> None:
    env_urls = os.environ.get("EVENT_URLS", "").strip()
    if not env_urls:
        raise SystemExit("EVENT_URLS fehlt (eine oder mehrere URLs).")

    urls = parse_urls(env_urls)
    prev = load_state()
    now = int(time.time())

    new_state = dict(prev)
    notified = 0

    for url in urls:
        try:
            html = fetch(url)
            state, info = detect_state(html)
        except Exception as e:
            print(f"[WARN] Fehler bei {url}: {e}")
            continue

        old_state = (prev.get(url) or {}).get("state")
        title = info.get("title", "")
        dt = info.get("datetime", "")

        # Notify nur beim Wechsel auf AVAILABLE
        if state == "AVAILABLE" and old_state != "AVAILABLE":
            msg = f"Tickets wieder verfügbar?\n\n{title}\n{dt}\n{url}"
            ntfy_notify(msg, title="Tickets verfügbar?")
            notified += 1
            print(f"[INFO] Benachrichtigung gesendet: {url}")

        new_state[url] = {
            "state": state,
            "title": title,
            "datetime": dt,
            "last_checked": now,
        }

        print(f"[OK] {url}  {old_state} -> {state}")

    save_state(new_state)
    print(f"[DONE] notified={notified}")
    


if __name__ == "__main__":
    main()

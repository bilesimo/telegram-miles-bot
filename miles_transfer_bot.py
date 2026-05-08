#!/usr/bin/env python3
"""Watch mileage-transfer promos and alert a Telegram chat."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_STATE_PATH = Path(__file__).with_name(".miles_transfer_state.json")
DEFAULT_OFFICIAL_LINK_PATTERNS = [
    r"^https://(?:www\.)?latampass\.latam\.com/",
    r"^https://(?:www\.)?voeazul\.com\.br/",
    r"^https://(?:www\d*\.)?livelo\.com\.br/",
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(part.strip() for part in self.parts if part.strip())


class AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.current_href: Optional[str] = None
        self.current_title: str = ""
        self.current_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return

        attributes = dict(attrs)
        self.current_href = (attributes.get("href") or "").strip()
        self.current_title = (attributes.get("title") or "").strip()
        self.current_parts = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self.current_href is None:
            return

        text = " ".join(part.strip() for part in self.current_parts if part.strip())
        if not text:
            text = self.current_title

        self.links.append((self.current_href, text))
        self.current_href = None
        self.current_title = ""
        self.current_parts = []


@dataclass
class FeedItem:
    source_name: str
    title: str
    link: str
    summary: str
    published: Optional[datetime]
    official_links: list[str] = field(default_factory=list)

    @property
    def stable_id(self) -> str:
        digest = hashlib.sha256(self.link.encode("utf-8")).hexdigest()
        return digest[:20]


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file at {path}. Copy config.example.json to config.json first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_ids": []}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen_ids": []}


def save_state(path: Path, seen_ids: Iterable[str], limit: int = 500) -> None:
    deduped = list(dict.fromkeys(seen_ids))
    payload = {"seen_ids": deduped[-limit:]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch_text(url: str, timeout_seconds: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MilesTransferBot/1.0 (+https://telegram.org/)",
            "Accept": "application/rss+xml, application/atom+xml, text/xml, application/xml, text/html",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        pass

    iso_value = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def strip_html(value: str) -> str:
    parser = TextExtractor()
    parser.feed(unescape(value))
    return re.sub(r"\s+", " ", parser.text()).strip()


def clean_title(value: str, cleanup_patterns: list[str]) -> str:
    cleaned = value
    for pattern in cleanup_patterns:
        cleaned = re.sub(pattern, "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned).strip(" -|")


def normalize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
    )


def matches_any_pattern(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value) for pattern in patterns)


def parse_rss_items(xml_text: str, source_name: str) -> list[FeedItem]:
    root = ET.fromstring(xml_text)
    items: list[FeedItem] = []

    # RSS 2.0
    for node in root.findall("./channel/item"):
        title = node.findtext("title", default="").strip()
        link = node.findtext("link", default="").strip()
        summary = node.findtext("description", default="").strip()
        published = parse_datetime(node.findtext("pubDate"))
        if title and link:
            items.append(
                FeedItem(
                    source_name=source_name,
                    title=strip_html(title),
                    link=link,
                    summary=strip_html(summary),
                    published=published,
                )
            )

    if items:
        return items

    # Atom
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    for node in root.findall("./atom:entry", namespace):
        title = node.findtext("atom:title", default="", namespaces=namespace).strip()
        summary = node.findtext("atom:summary", default="", namespaces=namespace).strip()
        published = parse_datetime(
            node.findtext("atom:published", default="", namespaces=namespace)
            or node.findtext("atom:updated", default="", namespaces=namespace)
        )
        link = ""
        for candidate in node.findall("atom:link", namespace):
            href = candidate.attrib.get("href", "").strip()
            rel = candidate.attrib.get("rel", "alternate")
            if href and rel == "alternate":
                link = href
                break
        if title and link:
            items.append(
                FeedItem(
                    source_name=source_name,
                    title=strip_html(title),
                    link=link,
                    summary=strip_html(summary),
                    published=published,
                )
            )
    return items


def parse_html_items(html_text: str, source: dict) -> list[FeedItem]:
    parser = AnchorExtractor()
    parser.feed(html_text)

    include_patterns = source.get("link_include_patterns", [])
    exclude_patterns = source.get("link_exclude_patterns", [])
    title_include_terms = [term.lower() for term in source.get("title_include_terms", [])]
    title_cleanup_patterns = source.get("title_cleanup_patterns", [])
    minimum_title_length = int(source.get("minimum_title_length", 20))

    items: list[FeedItem] = []
    seen_links: set[str] = set()
    for href, raw_title in parser.links:
        if not href:
            continue

        resolved_link = normalize_url(urllib.parse.urljoin(source["url"], href))
        title = clean_title(strip_html(raw_title), title_cleanup_patterns)
        haystack = f"{title} {resolved_link}".lower()

        if not title or len(title) < minimum_title_length:
            continue
        if include_patterns and not matches_any_pattern(resolved_link, include_patterns):
            continue
        if exclude_patterns and matches_any_pattern(resolved_link, exclude_patterns):
            continue
        if title_include_terms and not any(term in haystack for term in title_include_terms):
            continue
        if resolved_link in seen_links:
            continue

        seen_links.add(resolved_link)
        items.append(
            FeedItem(
                source_name=source["name"],
                title=title,
                link=resolved_link,
                summary="",
                published=None,
            )
        )

    return items


def extract_bonus(text: str) -> Optional[str]:
    match = re.search(r"(\d{1,3})\s*%", text)
    if not match:
        return None
    return match.group(1) + "%"


def is_transfer_promo(
    item: FeedItem,
    tracked_terms: list[str],
    transfer_terms: list[str],
    bonus_terms: list[str],
    negative_terms: list[str],
) -> bool:
    haystack = " ".join(
        [
            item.title.lower(),
            item.summary.lower(),
            item.link.lower(),
        ]
    )
    has_tracked_term = any(term.lower() in haystack for term in tracked_terms)
    has_transfer_term = any(term.lower() in haystack for term in transfer_terms)
    has_bonus_term = any(term.lower() in haystack for term in bonus_terms)
    has_negative_term = any(term.lower() in haystack for term in negative_terms)
    return has_tracked_term and has_transfer_term and has_bonus_term and not has_negative_term


def dedupe_items(items: list[FeedItem]) -> list[FeedItem]:
    selected: dict[str, FeedItem] = {}

    def item_score(item: FeedItem) -> tuple[int, int, int]:
        return (
            1 if item.published else 0,
            len(item.summary),
            len(item.title),
        )

    for item in items:
        current = selected.get(item.stable_id)
        if current is None or item_score(item) > item_score(current):
            selected[item.stable_id] = item

    return list(selected.values())


def extract_official_links(article_text: str, article_url: str, patterns: list[str]) -> list[str]:
    parser = AnchorExtractor()
    parser.feed(article_text)

    links: list[str] = []
    seen_links: set[str] = set()
    for href, _ in parser.links:
        if not href:
            continue

        resolved_link = normalize_url(urllib.parse.urljoin(article_url, href))
        if not matches_any_pattern(resolved_link, patterns):
            continue
        if resolved_link in seen_links:
            continue

        seen_links.add(resolved_link)
        links.append(resolved_link)

    return links


def confirm_official_links(
    article_url: str,
    timeout_seconds: int,
    official_link_patterns: list[str],
    transfer_terms: list[str],
    bonus_terms: list[str],
) -> list[str]:
    article_text = fetch_text(article_url, timeout_seconds)
    candidate_links = extract_official_links(article_text, article_url, official_link_patterns)

    confirmed_links: list[str] = []
    for link in candidate_links:
        try:
            official_text = fetch_text(link, timeout_seconds)
        except urllib.error.URLError:
            continue

        official_haystack = strip_html(official_text).lower()
        has_transfer_term = any(term.lower() in official_haystack for term in transfer_terms)
        has_bonus_term = any(term.lower() in official_haystack for term in bonus_terms)
        if has_transfer_term and has_bonus_term:
            confirmed_links.append(link)

    return confirmed_links[:3]


def format_message(item: FeedItem) -> str:
    bonus = extract_bonus(f"{item.title} {item.summary}")
    summary = item.summary[:280].strip()
    if len(item.summary) > 280:
        summary += "..."

    pieces = [
        "Miles transfer promo found",
        f"Program(s): LATAM / Azul / Livelo match",
        f"Source: {item.source_name}",
        f"Title: {item.title}",
    ]

    if bonus:
        pieces.append(f"Bonus spotted: {bonus}")

    if item.published:
        pieces.append(
            "Published: "
            + item.published.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        )

    if summary:
        pieces.append(f"Summary: {summary}")

    if item.official_links:
        pieces.append(f"Official page: {item.official_links[0]}")

    pieces.append(f"Link: {item.link}")
    return "\n".join(pieces)


def send_telegram_message(token: str, chat_id: str, text: str, timeout_seconds: int) -> None:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            raise RuntimeError(f"Telegram API rejected message: {body}")


def fetch_matching_items(config: dict) -> list[FeedItem]:
    timeout_seconds = int(config.get("timeout_seconds", 20))
    tracked_terms = config["tracked_terms"]
    transfer_terms = config["transfer_terms"]
    bonus_terms = config["bonus_terms"]
    negative_terms = config.get("negative_terms", [])
    matches: list[FeedItem] = []

    for source in config["sources"]:
        try:
            payload = fetch_text(source["url"], timeout_seconds)
            source_kind = source.get("kind", "feed")
            if source_kind == "feed":
                items = parse_rss_items(payload, source["name"])
            elif source_kind == "html":
                items = parse_html_items(payload, source)
            else:
                raise ValueError(f"Unsupported source kind: {source_kind}")
        except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
            print(f"Source failed: {source['name']} ({exc})", file=sys.stderr)
            continue

        for item in items:
            if is_transfer_promo(
                item,
                tracked_terms,
                transfer_terms,
                bonus_terms,
                negative_terms,
            ):
                matches.append(item)

    matches = dedupe_items(matches)
    matches.sort(key=lambda entry: entry.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return matches


def run_once(config_path: Path, state_path: Path, dry_run: bool = False) -> int:
    config = load_config(config_path)
    state = load_state(state_path)
    seen_ids = list(state.get("seen_ids", []))
    seen_lookup = set(seen_ids)

    matches = fetch_matching_items(config)
    new_items = [item for item in matches if item.stable_id not in seen_lookup]

    if not new_items:
        print("No new matching promos found.")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    timeout_seconds = int(config.get("timeout_seconds", 20))

    if not dry_run and (not token or not chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")

    official_link_patterns = config.get(
        "official_link_patterns",
        DEFAULT_OFFICIAL_LINK_PATTERNS,
    )
    transfer_terms = config["transfer_terms"]
    bonus_terms = config["bonus_terms"]

    for item in new_items:
        try:
            item.official_links = confirm_official_links(
                item.link,
                timeout_seconds,
                official_link_patterns,
                transfer_terms,
                bonus_terms,
            )
        except urllib.error.URLError as exc:
            print(f"Official confirmation failed for {item.link} ({exc})", file=sys.stderr)

        message = format_message(item)
        if dry_run:
            print("=" * 80)
            print(message)
        else:
            send_telegram_message(token, chat_id, message, timeout_seconds)
            print(f"Sent alert for: {item.title}")
        seen_ids.append(item.stable_id)

    save_state(state_path, seen_ids)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch RSS feeds for LATAM/Azul/Livelo transfer bonus promos and alert Telegram."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Config path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"State path (default: {DEFAULT_STATE_PATH})",
    )
    parser.add_argument(
        "--loop-minutes",
        type=int,
        default=0,
        help="If set to a positive number, rerun forever on that interval.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching alerts instead of sending them to Telegram.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.loop_minutes < 0:
        parser.error("--loop-minutes must be zero or positive")

    if args.loop_minutes == 0:
        return run_once(args.config, args.state, dry_run=args.dry_run)

    interval_seconds = args.loop_minutes * 60
    while True:
        try:
            run_once(args.config, args.state, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"Run failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

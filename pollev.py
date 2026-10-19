"""Poll Everywhere -> ntfy monitor for Linux, macOS, and Windows.

Install Playwright and Chromium, export NTFY_TOPIC, then run:
    python pollev.py

Useful checks (see README.md for Linuxlab deployment):
    python pollev.py --test-ntfy
    python pollev.py --test-html "saved_poll.html"
    python pollev.py --once

No login, passwords, or auto-answering. The browser reads the public
participant page, including its asynchronously loaded Turbo frame.
"""

import sys

if sys.version_info < (3, 10):
    print(
        "Python 3.10 or newer is required; this interpreter is Python %s. "
        "Recreate .venv with Python 3.11; see README.md."
        % sys.version.split()[0],
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path

# Keep your notification topic in the environment, outside Git history.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "REPLACE_WITH_YOUR_PRIVATE_NTFY_TOPIC")

POLLEV_URL = "https://pollev.com/athenatabakhi814?qr=1"
INTERVAL_SECONDS = 30
PAGE_TIMEOUT_MS = 25000
FRAME_LOAD_WAIT_MS = 2500
NOTIFY_ON_START_IF_OPEN = True
# Send an alert when an activity is displayed, even if its state cannot be decoded.
# This specifically covers `status=unknown` + `Activity displayed` in your logs.
NOTIFY_ON_VISIBLE_ACTIVITY = True
LOG_FILE = Path(__file__).with_name("pollev_notify.log")

# Poll Everywhere embeds current activity details via its participant UI.
# Example from the supplied answering-state HTML:
#   activity: {"activated":true,"web_enabled":true,"state":"opened",...}
ACTIVITY_SETUP_RE = re.compile(
    r"(?:window\.)?legacyActivityResponseView\.setup\s*\(\s*\{\s*activity\s*:\s*",
    re.IGNORECASE,
)
JSON_DECODER = json.JSONDecoder()


@dataclass
class CheckResult:
    status: str  # opened, closed, waiting, unknown, blocked
    activity_id: str = ""
    title: str = ""
    info: str = ""


def parse_current_activities(html: str):
    """Decode the actual `activity` JSON objects, not free-form page text."""
    for m in ACTIVITY_SETUP_RE.finditer(html):
        try:
            activity, _ = JSON_DECODER.raw_decode(html[m.end():])
        except json.JSONDecodeError:
            continue
        if isinstance(activity, dict):
            yield activity


def classify_html(html: str) -> CheckResult:
    """Classify the actual poll state. Never equate disabled vote buttons with closed."""
    lower = html.lower()
    blocked_signatures = (
        "verify you are human", "checking your browser", "attention required!",
        "just a moment...", "enable javascript and cookies to continue",
        "automated requests", "access denied", "cf-challenge",
    )
    if any(s in lower for s in blocked_signatures):
        return CheckResult("blocked", info="Page displayed access/verification challenge")

    activities = list(parse_current_activities(html))
    if activities:
        # The participant /active tab typically contains exactly one current activity.
        current = activities[-1]
        state = str(current.get("state") or "").lower()
        activated = current.get("activated")
        web_enabled = current.get("web_enabled")
        poll_id = str(current.get("id") or current.get("participant_key") or "")
        title = str(current.get("title") or "Poll Everywhere question")
        if state == "opened" and activated is not False and web_enabled is not False:
            return CheckResult("opened", poll_id, title, 'Embedded activity state="opened"')
        if state:
            return CheckResult("closed", poll_id, title, 'Embedded activity state="%s"' % state)

    # Fallback for activity types that do not have the legacy JSON bootstrap:
    # only count explicitly ENABLED answer inputs *inside the active tab frame*.
    active_part = html
    m = re.search(r'<turbo-frame\b[^>]*\bid=["\']active_tab_content["\'][^>]*>', html, re.I)
    if m:
        active_part = html[m.end():]
    else:
        active_part = ""  # Do not accidentally count navigation controls.

    has_question = bool(re.search(
        r'data-activity-type\s*=|component-response-activity|/[^\s"\']+/respond["\']',
        active_part, re.I,
    ))
    # The original saved poll has disabled choices after "Response recorded";
    # this fallback is deliberately conservative in that situation.
    enabled_vote_button = bool(re.search(
        r'<button\b(?=[^>]*component-response-[\w-]+__option__vote)(?![^>]*\bdisabled(?:\s|=|>))[^>]*>',
        active_part, re.I,
    ))
    enabled_answer_field = bool(re.search(
        r'<(?:textarea|input)\b(?=[^>]*(?:response|answer|vote))'
        r'(?![^>]*\bdisabled(?:\s|=|>))[^>]*>',
        active_part, re.I,
    ))
    if has_question and (enabled_vote_button or enabled_answer_field):
        title_match = re.search(
            r'<h1\b[^>]*class=["\'][^"\']*component-response-header__title[^"\']*["\'][^>]*>(.*?)</h1>',
            active_part, re.I | re.S,
        )
        title = unescape(re.sub(r"<[^>]+>", " ", title_match.group(1))).strip() if title_match else "New question"
        # Not a guaranteed unique ID if no embedded state is present.
        return CheckResult("opened", title or "unknown", title, "Fallback: enabled response input")

    # A rendered response activity is enough to alert the user. Some live pages
    # omit the legacyActivityResponseView.setup JSON, giving status=unknown.
    if has_question:
        return CheckResult("unknown", info="Activity displayed, but no confirmed opened state")
    if any(x in lower for x in ("waiting for", "presentation to begin", "no activities are currently active")):
        return CheckResult("waiting", info="Waiting for presentation/activity")
    return CheckResult("waiting", info="No current response activity found")


def send_ntfy(topic: str, title: str, message: str) -> None:
    topic = topic.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,128}", topic):
        raise ValueError("NTFY_TOPIC must be 5-128 letters, numbers, underscores or hyphens")
    req = urllib.request.Request(
        "https://ntfy.sh/" + urllib.parse.quote(topic, safe=""),
        data=message.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Title": title,  # ntfy header values must be ASCII-safe
            "Priority": "high",
            "Tags": "bell",
            "Click": POLLEV_URL,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError("ntfy returned HTTP %s" % response.status)


def run_browser_once(page) -> CheckResult:
    page.goto(POLLEV_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    # Turbo-frame contents are loaded asynchronously, and the main URL may not change.
    try:
        page.locator(
            '#active_tab_content .component-response-activity, '
            '#active_tab_content script:has-text("legacyActivityResponseView.setup")'
        ).first.wait_for(state="attached", timeout=FRAME_LOAD_WAIT_MS)
    except Exception:
        page.wait_for_timeout(900)
    return classify_html(page.content())


def make_parser():
    p = argparse.ArgumentParser(description="Notify phone when Poll Everywhere question opens")
    p.add_argument("--topic", help="Override the NTFY_TOPIC environment variable")
    p.add_argument("--interval", type=int, default=INTERVAL_SECONDS, help="Seconds between checks (default 30)")
    p.add_argument("--once", action="store_true", help="Check once and print result, without notifying")
    p.add_argument("--test-html", metavar="PATH", help="Classify a saved HTML file locally; no network")
    p.add_argument("--test-ntfy", action="store_true", help="Send a test push notification")
    p.add_argument("--show-browser", action="store_true", help="Show Chromium window (for debugging)")
    return p


def main():
    args = make_parser().parse_args()
    if args.test_html:
        html = Path(args.test_html).read_text(encoding="utf-8", errors="replace")
        r = classify_html(html)
        print('STATUS:', r.status, '| ID:', r.activity_id, '| TITLE:', r.title, '| REASON:', r.info)
        return 0 if r.status == "opened" or (NOTIFY_ON_VISIBLE_ACTIVITY and r.status == "unknown") else 1

    topic = args.topic or NTFY_TOPIC
    if args.test_ntfy:
        send_ntfy(topic, "PollEv test", "Poll Everywhere notifier is working.")
        print("Test push sent. Check the ntfy app subscription.")
        return 0

    if args.interval < 5:
        print("Minimum interval is 5 seconds.", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install with: python -m pip install playwright", file=sys.stderr)
        print("Then run:      python -m playwright install chromium", file=sys.stderr)
        return 2

    if not args.once and "REPLACE_WITH" in topic:
        print("Set NTFY_TOPIC first, or run with --topic YOUR_NTFY_TOPIC", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )
    previous_activity_id = ""
    # True after a push for this continuous active episode. This prevents
    # duplicate pushes if status changes unknown -> opened for one question.
    active_episode = False
    startup = True

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        try:
            while True:
                cycle_start = time.monotonic()
                try:
                    result = run_browser_once(page)
                    logging.info("status=%s poll=%s reason=%s", result.status, result.activity_id, result.info)
                    should_treat_as_active = (
                        result.status == "opened"
                        or (NOTIFY_ON_VISIBLE_ACTIVITY and result.status == "unknown")
                    )
                    if should_treat_as_active:
                        # The log sequence waiting -> unknown -> waiting is
                        # explicitly notification-worthy. Send at unknown.
                        # Only send again while active if both poll IDs are
                        # known and differ. Unknown -> opened is the same event.
                        different_known_activity = bool(
                            active_episode
                            and previous_activity_id
                            and result.activity_id
                            and result.activity_id != previous_activity_id
                        )
                        need_push = not active_episode or different_known_activity
                        notified = True
                        if need_push and (NOTIFY_ON_START_IF_OPEN or not startup) and not args.once:
                            label = ("question is OPEN" if result.status == "opened"
                                     else "activity detected")
                            title = "PollEv " + label
                            message = (
                                "Status: %s\n%s\n\nOpen: %s"
                                % (result.status, result.title or "Activity appeared on the page", POLLEV_URL)
                            )
                            try:
                                send_ntfy(topic, title, message)
                                logging.info("ntfy push sent for status=%s", result.status)
                            except Exception:
                                logging.exception("ntfy failed; will retry at next check")
                                notified = False
                        if notified:
                            active_episode = True
                            if result.activity_id:
                                previous_activity_id = result.activity_id
                        # Failed notifications are retried on the next cycle.
                    elif result.status in ("closed", "waiting"):
                        active_episode = False
                        previous_activity_id = ""
                    # For blocked pages and transient errors, keep episode state.
                    startup = False
                    if args.once:
                        print("RESULT:", result)
                        break
                except Exception:
                    logging.exception("Poll Everywhere check failed")
                    if args.once:
                        return 1
                time.sleep(max(0.0, args.interval - (time.monotonic() - cycle_start)))
        except KeyboardInterrupt:
            logging.info("Stopped by user")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests>=2.31",
#   "PyYAML>=6.0",
# ]
# ///
# if pypi.org blocked --index-url https://us-python.pkg.dev/pypi-packages/pypi/simple
"""
PagerDuty CLI - List and manage incidents from the command line.

Usage:
    pdcli.py                       # List incidents grouped by status
    pdcli.py -a, --ack-all         # Acknowledge all triggered incidents
    pdcli.py -b, --background-ack  # Daemon: auto-ack incidents older than interval
    pdcli.py -i, --interval MIN    # Set interval in minutes (default: 3)
    pdcli.py --test-alert          # Test terminal and macOS notifications

Examples:
    pdcli.py -b                    # Background mode with 3 min interval
    pdcli.py -b -i 5               # Background mode with 5 min interval

Config: ~/.config/pagerduty_tui.yaml
    pagerduty_api_key: <your-api-key>
    pagerduty_domain: <your-org>
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

PAGERDUTY_API_URL = "https://api.pagerduty.com"
DEFAULT_INTERVAL_MINUTES = 3


def load_config() -> dict:
    """Load config from ~/.config/pagerduty_tui.yaml"""
    config_path = Path.home() / ".config" / "pagerduty_tui.yaml"
    if not config_path.exists():
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        return yaml.safe_load(f)


def get_headers(api_key: str) -> dict:
    """Build PagerDuty API headers."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token token={api_key}",
    }


def get_current_user_id(api_key: str) -> str:
    """Get the current user's PagerDuty ID from the API key."""
    resp = requests.get(
        f"{PAGERDUTY_API_URL}/users/me",
        headers=get_headers(api_key),
    )
    resp.raise_for_status()
    return resp.json()["user"]["id"]


def get_incidents(api_key: str, user_id: str) -> list[dict]:
    """Fetch all triggered and acknowledged incidents for the user."""
    incidents = []
    for status in ["triggered", "acknowledged"]:
        resp = requests.get(
            f"{PAGERDUTY_API_URL}/incidents",
            headers=get_headers(api_key),
            params={
                "statuses[]": status,
                "user_ids[]": user_id,
                "limit": 100,
            },
        )
        resp.raise_for_status()
        incidents.extend(resp.json()["incidents"])
    return incidents


def acknowledge_incident(api_key: str, incident_id: str) -> bool:
    """Acknowledge a single incident. Returns True on success."""
    resp = requests.put(
        f"{PAGERDUTY_API_URL}/incidents/{incident_id}",
        headers=get_headers(api_key),
        json={
            "incident": {
                "type": "incident_reference",
                "status": "acknowledged",
            }
        },
    )
    return resp.status_code == 200


def parse_created_at(created_at: str) -> datetime:
    """Parse PagerDuty ISO timestamp to datetime."""
    # Handle both formats: with and without microseconds
    created_at = created_at.replace("Z", "+00:00")
    return datetime.fromisoformat(created_at)


def get_incident_age_minutes(incident: dict) -> float:
    """Get incident age in minutes."""
    created = parse_created_at(incident["created_at"])
    now = datetime.now(timezone.utc)
    return (now - created).total_seconds() / 60


def send_terminal_notification(title: str, body: str) -> None:
    """Send notification via OSC 99 (kitty/ghostty protocol)."""
    # OSC 99 notification: \x1b]99;i=1:d=0;{title}\x1b\\
    # For kitty/ghostty, use the simpler OSC 9 for broader compat
    notification = f"\x1b]9;{title}: {body}\x07"
    sys.stdout.write(notification)
    sys.stdout.flush()

    # Also try OSC 99 for kitty specifically
    # Format: ESC ] 99 ; i=<id>:d=0:p=body ; <payload> ST
    kitty_notif = f"\x1b]99;i=1:d=0;{body}\x1b\\"
    sys.stdout.write(kitty_notif)
    sys.stdout.flush()


def send_macos_notification(title: str, body: str) -> None:
    """Send macOS notification via osascript."""
    script = f'display notification "{body}" with title "{title}"'
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
    )


def send_notification(title: str, body: str) -> None:
    """Send both terminal and macOS notifications."""
    send_terminal_notification(title, body)
    send_macos_notification(title, body)


def format_incident(incident: dict) -> str:
    """Format a single incident for display."""
    service = incident.get("service", {}).get("summary", "Unknown")
    summary = incident.get("summary", "No summary")[:80]
    created = incident.get("created_at", "")[:19].replace("T", " ")
    assignee = "Unassigned"
    if incident.get("assignments"):
        assignee = incident["assignments"][0].get("assignee", {}).get("summary", "Unknown")

    priority = ""
    if incident.get("priority"):
        priority = " [P1]"

    return f"  {incident['id']}{priority}\n    {service}\n    {summary}\n    Created: {created} | Assignee: {assignee}"


def list_incidents(api_key: str) -> None:
    """List all incidents grouped by status."""
    user_id = get_current_user_id(api_key)
    incidents = get_incidents(api_key, user_id)

    triggered = [i for i in incidents if i["status"] == "triggered"]
    acknowledged = [i for i in incidents if i["status"] == "acknowledged"]

    print("\n" + "=" * 60)
    print(f"TRIGGERED ({len(triggered)})")
    print("=" * 60)
    if triggered:
        for inc in triggered:
            print(format_incident(inc))
            print()
    else:
        print("  No triggered incidents")

    print("\n" + "=" * 60)
    print(f"ACKNOWLEDGED ({len(acknowledged)})")
    print("=" * 60)
    if acknowledged:
        for inc in acknowledged:
            print(format_incident(inc))
            print()
    else:
        print("  No acknowledged incidents")

    print()


def ack_all(api_key: str) -> None:
    """Acknowledge all triggered incidents."""
    user_id = get_current_user_id(api_key)
    incidents = get_incidents(api_key, user_id)
    triggered = [i for i in incidents if i["status"] == "triggered"]

    if not triggered:
        print("No triggered incidents to acknowledge.")
        return

    print(f"Acknowledging {len(triggered)} incident(s)...")
    for inc in triggered:
        if acknowledge_incident(api_key, inc["id"]):
            print(f"  Acknowledged: {inc['id']} - {inc.get('summary', '')[:50]}")
        else:
            print(f"  FAILED: {inc['id']}")

    print("Done.")


def format_incident_oneline(incident: dict) -> str:
    """Format incident for background-ack log output."""
    inc_number = incident.get("incident_number", incident["id"])
    service = incident.get("service", {}).get("summary", "Unknown")
    summary = incident.get("summary", "No summary")[:60]
    created = incident.get("created_at", "")[:19].replace("T", " ")
    assignee = "Unassigned"
    if incident.get("assignments"):
        assignee = incident["assignments"][0].get("assignee", {}).get("summary", "Unknown")
    return f"[#{inc_number}] {service}: {summary}\n    Created: {created} | Assignee: {assignee}"


def background_ack(api_key: str, interval_minutes: int = DEFAULT_INTERVAL_MINUTES) -> None:
    """Run as daemon, auto-ack incidents older than the specified interval."""
    sleep_seconds = interval_minutes * 60
    print(f"Background ack daemon started. Auto-ack threshold: {interval_minutes} min")
    print(f"Checking every {interval_minutes} minutes. Press Ctrl+C to stop.\n")

    user_id = get_current_user_id(api_key)

    while True:
        try:
            incidents = get_incidents(api_key, user_id)
            triggered = [i for i in incidents if i["status"] == "triggered"]

            for inc in triggered:
                age_min = get_incident_age_minutes(inc)
                if age_min >= interval_minutes:
                    if acknowledge_incident(api_key, inc["id"]):
                        timestamp = datetime.now().strftime('%H:%M:%S')
                        details = format_incident_oneline(inc)
                        print(f"[{timestamp}] Auto-acked ({age_min:.1f} min old):")
                        print(f"    {details}")
                        print()
                        # Notification with summary
                        service = inc.get("service", {}).get("summary", "")
                        summary = inc.get("summary", "")[:50]
                        send_notification("PagerDuty Auto-Ack", f"{service}: {summary}")

            time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            print("\nStopping background ack daemon.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(sleep_seconds)


def test_alert() -> None:
    """Test terminal and macOS notifications."""
    print("Sending test notifications...")
    send_notification("PagerDuty Test", "This is a test notification from pd-cli.py")
    print("Done. Check your terminal and macOS notification center.")


def main():
    parser = argparse.ArgumentParser(
        description="PagerDuty CLI - List and manage incidents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-a", "--ack-all",
        action="store_true",
        help="Acknowledge all triggered incidents",
    )
    parser.add_argument(
        "-b", "--background-ack",
        action="store_true",
        help="Daemon mode: auto-ack incidents older than interval",
    )
    parser.add_argument(
        "-i", "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MINUTES,
        metavar="MIN",
        help=f"Interval in minutes for background-ack (default: {DEFAULT_INTERVAL_MINUTES})",
    )
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Test terminal and macOS notifications",
    )

    args = parser.parse_args()

    if args.test_alert:
        test_alert()
        return

    config = load_config()
    api_key = config.get("pagerduty_api_key")
    if not api_key:
        print("Error: pagerduty_api_key not found in config")
        sys.exit(1)

    if args.ack_all:
        ack_all(api_key)
    elif args.background_ack:
        background_ack(api_key, args.interval)
    else:
        list_incidents(api_key)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Drive the IT office alert light from the Device Inventory help desk (Raspberry Pi).

A relay on --relay-pin switches the light; a push button on --button-pin silences it.
Every few seconds this asks Device Inventory (/helpdesk/light/status) whether the light should
be on: it is while an open ticket has an alert nobody has silenced (see Help Desk → Office
light). Pressing the button posts to /helpdesk/light/ack and turns the light off at once.

Pins use BCM numbering (GPIO17 is physical pin 11, GPIO27 is pin 13). Many relay boards
switch on when their input is LOW; use --relay-active-low for those.

Examples:
  python3 helpdesk_light.py                    # Pi is also the Device Inventory server
  python3 helpdesk_light.py --test             # click the relay on for 3 seconds, then exit
  python3 helpdesk_light.py --url http://10.0.0.5:5000 --key KEY   # server on another computer
  python3 helpdesk_light.py --quiet-hours 17:30-07:00             # never on overnight
  python3 helpdesk_light.py --dry-run          # no GPIO: print what the light would do
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime


def parse_args(argv=None):
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default=env("HELPDESK_URL", "http://127.0.0.1:5000"),
                   help="Device Inventory's address (default: this Pi, port 5000)")
    p.add_argument("--key", default=env("HELPDESK_KEY", ""), help="the key from Help Desk → Office light "
                   "(only needed when Device Inventory runs on another computer)")
    p.add_argument("--relay-pin", type=int, default=int(env("RELAY_PIN", "17")), help="BCM pin (default 17)")
    p.add_argument("--button-pin", type=int, default=int(env("BUTTON_PIN", "27")),
                   help="BCM pin, button to ground (default 27); 0 = no button")
    p.add_argument("--relay-active-low", action="store_true", default=env("RELAY_ACTIVE_LOW") == "1",
                   help="the relay switches on when its input is LOW")
    p.add_argument("--interval", type=float, default=float(env("INTERVAL", "3")), help="seconds between checks")
    p.add_argument("--quiet-hours", default=env("QUIET_HOURS", ""),
                   help='keep the light off during these hours, e.g. "17:30-07:00"')
    p.add_argument("--test", action="store_true", help="turn the relay on for 3 seconds, then exit")
    p.add_argument("--dry-run", action="store_true", help="don't use GPIO; print what would happen")
    return p.parse_args(argv)


def in_quiet_hours(spec, now=None):
    """Whether `now` falls inside "HH:MM-HH:MM" (which may run past midnight)."""
    if not spec:
        return False
    now = now or datetime.now()
    try:
        start, end = ((int(h), int(m)) for h, m in (part.split(":") for part in spec.split("-")))
    except ValueError:
        raise SystemExit(f'--quiet-hours should look like "17:30-07:00", not "{spec}"')
    t = (now.hour, now.minute)
    return start <= t < end if start <= end else (t >= start or t < end)


class Hub:
    """Talks to Device Inventory."""

    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    def _call(self, path, method="GET"):
        req = urllib.request.Request(self.url + path, method=method, data=b"" if method == "POST" else None,
                                     headers={"X-Helpdesk-Key": self.key} if self.key else {})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)

    def status(self):
        return self._call("/helpdesk/light/status")

    def silence(self):
        return self._call("/helpdesk/light/ack", "POST")


class PrintRelay:
    def __init__(self):
        self.is_active = False

    def on(self):
        self.is_active = True
        print("[light ON]", flush=True)

    def off(self):
        self.is_active = False
        print("[light off]", flush=True)


def make_relay(args):
    if args.dry_run:
        return PrintRelay()
    try:
        from gpiozero import OutputDevice
    except ImportError:
        raise SystemExit("gpiozero isn't installed. On Raspberry Pi OS: sudo apt install python3-gpiozero")
    return OutputDevice(args.relay_pin, active_high=not args.relay_active_low, initial_value=False)


def make_button(args, on_press):
    if args.dry_run or not args.button_pin:
        return None
    from gpiozero import Button
    button = Button(args.button_pin, pull_up=True, bounce_time=0.1)
    button.when_pressed = on_press
    return button


_KEEP = {}


def run(args, hub=None, relay=None, cycles=None, sleep=time.sleep):
    hub = hub or Hub(args.url, args.key)
    relay = relay or make_relay(args)
    if args.test:
        relay.on()
        sleep(3)
        relay.off()
        return 0
    lock = threading.Lock()
    trouble = {"shown": False}

    def set_light(want):
        with lock:
            if want and not relay.is_active:
                relay.on()
            elif not want and relay.is_active:
                relay.off()

    def pressed():
        set_light(False)  # off immediately, even if the server is slow
        try:
            hub.silence()
            print("Button: silenced.", flush=True)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"Button pressed, but Device Inventory couldn't be told: {e}", flush=True)

    _KEEP["button"] = make_button(args, pressed)  # must stay referenced, or it stops listening
    print(f"Watching {hub.url} every {args.interval:g} s (relay on GPIO{args.relay_pin}"
          + (f", button on GPIO{args.button_pin}" if args.button_pin else "") + ").", flush=True)
    n = 0
    while cycles is None or n < cycles:
        n += 1
        try:
            state = hub.status()
            if trouble["shown"]:
                print("Connected to Device Inventory again.", flush=True)
                trouble["shown"] = False
            want = bool(state.get("light")) and not in_quiet_hours(args.quiet_hours)
            if want and not relay.is_active:
                ticket = state.get("ticket") or {}
                print(f"Light on: {state.get('reason_text')}"
                      + (f", #{ticket.get('id')} {ticket.get('subject')}" if ticket else ""), flush=True)
            set_light(want)
        except urllib.error.HTTPError as e:
            set_light(False)
            if not trouble["shown"]:
                hint = " (wrong or missing --key?)" if e.code == 403 else ""
                print(f"Device Inventory answered {e.code}{hint}; light off until it works.", flush=True)
                trouble["shown"] = True
        except (urllib.error.URLError, OSError, ValueError) as e:
            set_light(False)  # never leave the light stuck on
            if not trouble["shown"]:
                print(f"Can't reach Device Inventory at {hub.url} ({e}); light off, retrying.", flush=True)
                trouble["shown"] = True
        sleep(args.interval)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run(parse_args()))
    except KeyboardInterrupt:
        sys.exit(0)

#!/usr/bin/env python3
"""
Cancel AAC jobGroups that have been running longer than X hours.
 
Dry-run by default: lists what WOULD be cancelled. Pass --execute to cancel.
 
Usage:
    export AAC_BASE_URL="https://us1.alteryxcloud.com"
    export AAC_TOKEN="eyJ0b2tlbklkIjoiOGIwY2U1YzktNmMwZi00YzkzLWI3OWUtNTUyNDc4ZWM0YmUwIiwic2VjcmV0IjoiNDI3MWFmZDFiZTcwZTQxMGJhN2Q4MDA4OTQyYmExOTNmMTgyZGM2MTJmMjg0ZTZkYzAwZGUzNmE3NjFlNWE5MyJ9"
    python cancel_stale_jobs.py --hours 12            # dry run
    python cancel_stale_jobs.py --hours 12 --execute  # actually cancel
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
 
import requests
 
if sys.version_info < (3, 6):
    sys.exit("This script requires Python 3.6 or newer.")

ACTIVE_STATUSES = {"Created", "Pending", "InProgress"}
PAGE_SIZE = 25
 
 
def parse_ts(value):
    """Parse ISO timestamps like 2026-11-06T09:12:33.000Z."""
    if value is None:
        raise ValueError("Missing timestamp value")

    value = str(value).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    # Python 3.7+ has datetime.fromisoformat; Python 3.6 does not.
    if hasattr(datetime, "fromisoformat"):
        return datetime.fromisoformat(value)

    # Python 3.6 compatibility: normalize timezone offset to +HHMM before parsing.
    # Example: 2026-11-06T09:12:33.000+00:00 -> 2026-11-06T09:12:33.000+0000
    if len(value) >= 6 and value[-3] == ":" and value[-6] in "+-":
        value = value[:-3] + value[-2:]

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError("Unsupported timestamp format: {}".format(value))

 
def list_job_groups(session, base_url, lookback_days):
    """Page through jobGroups newest-first; stop once older than the lookback window."""
    stop_before = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    offset = 0
    while True:
        resp = session.get(
            "{}/v4/jobGroups".format(base_url),
            params={
                "fields": "id;name;status;createdAt;updatedAt",
                "embed": "creator,wrangledDataset",
                "sort": "-createdAt",
                "limit": PAGE_SIZE,
                "offset": offset,
            },
            timeout=60,
        )
        resp.raise_for_status()
        page = resp.json().get("data", [])
        if not page:
            return
        for jg in page:
            if parse_ts(jg["createdAt"]) < stop_before:
                return
            yield jg
        offset += PAGE_SIZE
 
 
def cancel_job_group(session, base_url, jg_id):
    resp = session.post(f"{base_url}/v4/jobGroups/{jg_id}/cancel", json={}, timeout=60)
    return resp.status_code, resp.text[:200]
 
 
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hours", type=float, required=True, help="Cancel jobs running longer than this many hours")
    p.add_argument("--lookback-days", type=float, default=150,
                   help="Only scan jobGroups created within this many days (default 150)")
    p.add_argument("--statuses", default="InProgress",
                   help=f"Comma-separated statuses to treat as running (default InProgress; options: {','.join(sorted(ACTIVE_STATUSES))})")
    p.add_argument("--execute", action="store_true", help="Actually cancel (default is dry run)")
    p.add_argument("--base-url", default=os.environ.get("AAC_BASE_URL"))
    p.add_argument("--token", default=os.environ.get("AAC_TOKEN"))
    args = p.parse_args()
 
    if not args.base_url or not args.token:
        sys.exit("Set AAC_BASE_URL and AAC_TOKEN (or pass --base-url / --token).")
 
    statuses = {s.strip() for s in args.statuses.split(",") if s.strip()}
    base_url = args.base_url.rstrip("/")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
 
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {args.token}", "Accept": "application/json"})
 
    stale = [
        jg for jg in list_job_groups(session, base_url, args.lookback_days)
        if jg.get("status") in statuses and parse_ts(jg["createdAt"]) < cutoff
    ]
 
    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"[{mode}] {len(stale)} jobGroup(s) in {sorted(statuses)} running > {args.hours}h\n")
    now = datetime.now(timezone.utc)
    for jg in stale:
        age_h = (now - parse_ts(jg["createdAt"])).total_seconds() / 3600
        creator = (jg.get("creator") or {}).get("email", "?")
        print(f"  {jg['id']:>10}  {jg['status']:<10}  {age_h:7.1f}h  {creator}  {jg.get('name') or ''}")
 
    if not args.execute:
        print("\nNothing cancelled. Re-run with --execute to cancel these.")
        return
 
    print()
    failures = 0
    for jg in stale:
        code, body = cancel_job_group(session, base_url, jg["id"])
        ok = 200 <= code < 300
        failures += not ok
        print(f"  cancel {jg['id']}: {'OK' if ok else f'FAILED {code} {body}'}")
    print(f"\nDone: {len(stale) - failures} cancelled, {failures} failed.")
    sys.exit(1 if failures else 0)
 
 
if __name__ == "__main__":
    main()
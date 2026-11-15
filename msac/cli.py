"""Command-line interface:  python -m msac <command>"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from . import analysis, config, mechanisms, pipeline
from .storage import Store
from .userdata import UserStore


def _store(args) -> Store:
    return Store(args.db)


def cmd_ingest(args) -> None:
    store = _store(args)
    since = date.fromisoformat(args.since) if args.since else None
    summary = pipeline.ingest(store, args.sources, since=since, max_results=args.max)
    print("Ingested:", summary)
    print("Totals:", store.stats())
    if "clinicaltrials" in args.sources:
        # Saved items live in a separate database; only their status history is updated here.
        users = UserStore(args.user_db)
        changes = users.sync_trial_statuses(store)
        for c in changes:
            print(f"Saved trial {c['id']} changed status: {c['from']} -> {c['to']}")
        print(f"Saved trials checked; {len(changes)} status change(s).")
        users.close()


def cmd_retag(args) -> None:
    n_a, n_t = pipeline.retag(_store(args))
    print(f"Re-annotated {n_a} articles and {n_t} trials.")


def cmd_mechanisms(args) -> None:
    ranked = analysis.rank_mechanisms(_store(args), kinds=None if args.all else ("mechanism",))
    print("Results are positive/negative/mixed/unclear. Trials are recruiting/completed/stopped.\n")
    for i, s in enumerate(ranked[: args.top], 1):
        r, t = s.results, s.trial_counts
        fmt = lambda d: "/".join(str(d[k]) for k in analysis.OUTCOME_KEYS)  # noqa: E731
        print(f"{i:>2}. {s.grade}  {s.mechanism.name}\n      {s.grade_meaning}\n      Why: {s.grade_reason}")
        print(f"      Human {fmt(r['human'])} | Animal {fmt(r['animal'])} | In vitro {fmt(r['in_vitro'])} | "
              f"Trials {t['recruiting']}/{t['completed']}/{t['stopped']}\n")
    print(config.DISCLAIMER)


def cmd_top_trials(args) -> None:
    rows = analysis.rank_open_trials(_store(args))
    for i, r in enumerate(rows[: args.top], 1):
        t = r["trial"]
        print(f"{i:>2}  {r['score']:>5}  {t.nct_id}  {t.phase_label:<18} {r['target']:<38} {t.title[:70]}")
    print("\n" + config.DISCLAIMER)


def cmd_trials(args) -> None:
    store = _store(args)
    rows = analysis.filter_trials(
        list(store.iter_trials()), status=args.status, country=args.country, subtype_fit=args.subtype_fit,
        age=args.age, lat=args.lat, lon=args.lon)
    for t, km in rows:
        dist = f"{km} km" if km is not None else ""
        print(f"{t.nct_id}  {t.status_label:<22} {t.phase_label:<16} "
              f"{mechanisms.SUBTYPE_FIT_LABELS[t.subtype_fit]:<36} {dist:>8}  {t.title[:80]}")
    print(f"\n{len(rows)} trials.")


def cmd_trial(args) -> None:
    store = _store(args)
    t = store.get_trial(args.nct_id.upper())
    if t is None:
        print("Trial not found in local database. Run `ingest` first.")
        return
    info = analysis.enrollment_info(t, args.lat, args.lon)
    print(f"{t.nct_id}: {t.title}\nStatus: {info['status']}   MSA-C fit: {info['subtype_fit']}")
    print(f"Registry page: {info['registry_url']}\nAge: {info['age_range']}   Sex: {info['sex']}")
    print("\nContacts:")
    for c in info["contacts"] or [{"name": "None listed - use the registry page"}]:
        print("  -", ", ".join(v for v in (c.get("name"), c.get("phone"), c.get("email")) if v))
    print("\nOpen sites:")
    for s in info["sites"][:10]:
        dist = f" ({s['distance_km']} km)" if "distance_km" in s else ""
        print(f"  - {s['facility']}, {s['city']}, {s['country']}{dist}")
    print("\n" + config.DISCLAIMER)


def cmd_stats(args) -> None:
    store = _store(args)
    print(store.stats())
    for r in store.recent_runs():
        print(dict(r))


def cmd_vapid(args):
    from .push import generate_keys
    generate_keys(args.output)


def cmd_test_push(args):
    from .push import send_test_push
    send_test_push()


def cmd_maintenance(args):
    from . import maintenance
    if args.command == 'backup':
        print(maintenance.backup(args.db, args.user_db, args.backup_dir))
    elif args.command == 'refresh-scheduled':
        from .static_build import scheduled
        scheduled(args.db,args.user_db,out=args.out,backup_dir=args.backup_dir,force=args.force)
    else:
        maintenance.refresh(args.db, args.user_db,
                            'trials' if args.command == 'refresh-trials' else 'papers',
                            backup_dir=args.backup_dir, meta_path=args.meta,
                            public_dir=args.previous,notify=not args.no_notify)


def cmd_export_static(args):
    from .export import export_static
    meta = export_static(args.db,args.out,build_time=args.build_time,heartbeat=args.heartbeat)
    print('Exported:',meta['counts'])


def cmd_check_trials(args):
    from .static_build import check_trials
    raise SystemExit(0 if check_trials(args.out) else 3)


def cmd_notify(args):
    from .maintenance import maintenance_lock,initialize
    from .push import send_pending
    with maintenance_lock(args.db):
        store = Store(args.db)
        try:
            initialize(store)
            send_pending(store)
        finally:
            store.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="msac", description="MSA-C research navigator")
    p.add_argument("--db", default=str(config.DB_PATH), help="Research database path (rebuilt by ingest)")
    p.add_argument("--user-db", default=None, help="Saved items and settings database path "
                   "(default: user.sqlite3 next to the research database)")
    sub = p.add_subparsers(dest="command", required=True)

    for command in ('refresh-trials', 'refresh-papers', 'refresh-scheduled', 'backup'):
        s = sub.add_parser(command, help='Serialized portable maintenance')
        s.add_argument('--backup-dir', default=str(config.APP_DIR / 'backups'))
        s.add_argument('--meta', default=str(config.DATA_DIR / 'maintenance-internal.json'),
                       help='Private diagnostic metadata; never use a public export path')
        s.add_argument('--previous', default='public-data', help='Previous public export for trial status history')
        s.add_argument('--no-notify', action='store_true',help='Defer notification delivery until after export')
        if command == 'refresh-scheduled':
            s.add_argument('--out',default='public-data')
            s.add_argument('--force',action='store_true',help='Recompute/export even if no source is due')
        s.set_defaults(func=cmd_maintenance)
    s = sub.add_parser('export-static',help='Write and validate the complete public JSON contract')
    s.add_argument('--out',default='public-data')
    s.add_argument('--build-time',help='UTC ISO-8601 build timestamp (default: latest research update)')
    s.add_argument('--heartbeat',action='store_true')
    s.set_defaults(func=cmd_export_static)
    s = sub.add_parser('check-trials',help='Exit 0 changed, 3 unchanged, 1 API/error')
    s.add_argument('--out',default='public-data',help='Previous export directory')
    s.set_defaults(func=cmd_check_trials)
    s = sub.add_parser('notify',help='Deliver queued push events after a successful export')
    s.set_defaults(func=cmd_notify)
    s = sub.add_parser('test-push',help='Explicitly send one test notification to PUSH_SUBSCRIPTION')
    s.set_defaults(func=cmd_test_push)
    s = sub.add_parser('generate-vapid', help='Print a VAPID key pair for GitHub secrets; no file is created')
    s.add_argument('--output',help='Optional exclusive output file outside any repository')
    s.set_defaults(func=cmd_vapid)

    s = sub.add_parser("ingest", help="Fetch research and trials from the internet")
    s.add_argument("--sources", nargs="+", default=list(pipeline.ALL_SOURCES), choices=pipeline.ALL_SOURCES)
    s.add_argument("--since", help="Only literature added since YYYY-MM-DD (incremental update)")
    s.add_argument("--max", type=int, help="Max records per source (for quick tests)")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("retag", help="Re-run classifiers after editing the taxonomy")
    s.set_defaults(func=cmd_retag)

    s = sub.add_parser("mechanisms", help="Rank biological mechanisms")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--all", action="store_true", help="Include biomarker and symptomatic categories")
    s.set_defaults(func=cmd_mechanisms)

    for name, func in (("trials", cmd_trials), ("trial", cmd_trial)):
        s = sub.add_parser(name, help="List trials" if name == "trials" else "Show how to join one trial")
        if name == "trial":
            s.add_argument("nct_id")
        else:
            s.add_argument("--status", choices=["open", "active", "all"], default="open")
            s.add_argument("--country")
            s.add_argument("--subtype-fit", choices=list(mechanisms.SUBTYPE_FIT_LABELS))
            s.add_argument("--age", type=float)
        s.add_argument("--lat", type=float, help="Your latitude, to sort sites by distance")
        s.add_argument("--lon", type=float)
        s.set_defaults(func=func)

    s = sub.add_parser("top-trials", help="Rank recruiting treatment trials by how promising they look")
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(func=cmd_top_trials)

    s = sub.add_parser("stats", help="Database totals and recent ingest runs")
    s.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    if args.user_db is None:
        args.user_db = str(config.USER_DB_PATH if args.db == str(config.DB_PATH) else Path(args.db).parent / "user.sqlite3")
    if args.command in ('ingest', 'retag'):
        from .maintenance import maintenance_lock
        with maintenance_lock(args.db):
            args.func(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()

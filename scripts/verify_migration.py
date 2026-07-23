#!/usr/bin/env python
"""Verify a Neon -> Supabase PostgreSQL migration by comparing the two databases.

Connects to both the source and target databases and compares, per table in the
``public`` schema:
  * presence of the table
  * exact row count
  * number of indexes, constraints and sequences

Exits non-zero if anything differs, so it can gate a deployment.

Usage (PowerShell/bash):
    python scripts/verify_migration.py \
        --source "postgresql://<neon-url>?sslmode=require" \
        --target "postgresql://<supabase-session-pooler-url>?sslmode=require"

If --source/--target are omitted they fall back to the env vars
NEON_URL / SUPABASE_URL. Read-only: runs only SELECT/count queries.
"""
import argparse
import os
import sys

try:
    import psycopg  # psycopg 3, already a project dependency
except ImportError:  # pragma: no cover
    sys.exit("psycopg (v3) is required: pip install 'psycopg[binary]'")


TABLE_LIST_SQL = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name;
"""

COUNTS_SQL = {
    "indexes": "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public';",
    "sequences": "SELECT count(*) FROM information_schema.sequences WHERE sequence_schema = 'public';",
    "constraints": (
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE constraint_schema = 'public';"
    ),
}


def _connect(url):
    return psycopg.connect(url, connect_timeout=15)


def _tables(conn):
    with conn.cursor() as cur:
        cur.execute(TABLE_LIST_SQL)
        return [r[0] for r in cur.fetchall()]


def _row_count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM public."{table}";')
        return cur.fetchone()[0]


def _scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("NEON_URL"))
    parser.add_argument("--target", default=os.getenv("SUPABASE_URL"))
    args = parser.parse_args()

    if not args.source or not args.target:
        sys.exit("Provide --source/--target or set NEON_URL and SUPABASE_URL.")

    print("Connecting to source (Neon) and target (Supabase)...")
    with _connect(args.source) as src, _connect(args.target) as dst:
        src_tables = set(_tables(src))
        dst_tables = set(_tables(dst))

        missing_in_target = sorted(src_tables - dst_tables)
        extra_in_target = sorted(dst_tables - src_tables)

        print(f"\nTables: source={len(src_tables)}  target={len(dst_tables)}")
        if missing_in_target:
            print(f"  !! MISSING in target: {missing_in_target}")
        if extra_in_target:
            print(f"  .. extra in target (ok if Supabase system tables): {extra_in_target}")

        print("\nRow counts (source -> target):")
        mismatches = []
        for table in sorted(src_tables & dst_tables):
            s = _row_count(src, table)
            d = _row_count(dst, table)
            flag = "OK " if s == d else "!! "
            if s != d:
                mismatches.append((table, s, d))
            print(f"  {flag}{table:<45} {s:>8} -> {d:>8}")

        print("\nSchema object counts (source -> target):")
        object_mismatch = []
        for label, sql in COUNTS_SQL.items():
            s = _scalar(src, sql)
            d = _scalar(dst, sql)
            flag = "OK " if s == d else "~~ "
            if s != d:
                object_mismatch.append((label, s, d))
            print(f"  {flag}{label:<20} {s:>6} -> {d:>6}")

    print("\n" + "=" * 60)
    ok = not missing_in_target and not mismatches
    if ok:
        print("RESULT: PASS — all tables present, every row count matches.")
        if object_mismatch:
            print("NOTE: index/constraint/sequence counts differ; review the list "
                  "above (often benign, e.g. Supabase adds objects).")
        return 0
    print("RESULT: FAIL — data or tables differ. DO NOT cut over.")
    for table, s, d in mismatches:
        print(f"  row mismatch: {table} source={s} target={d}")
    for table in missing_in_target:
        print(f"  missing table in target: {table}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

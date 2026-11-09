"""
05_metadata.py — Catalog introspection via conn.meta
=====================================================
Demonstrates the metadata API for exploring Netezza system catalog views.
Requires a running Netezza instance.  Set env vars:
  NZ_DEV_HOST / NZ_DEV_PORT / NZ_DEV_DB / NZ_DEV_USER / NZ_DEV_PASSWORD
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import nzpy_extended as nzpy

NZ_HOST = os.environ.get("NZ_DEV_HOST", "127.0.0.1")
NZ_PORT = int(os.environ.get("NZ_DEV_PORT", "5480"))
NZ_DB = os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER = os.environ.get("NZ_DEV_USER", "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD", "password")


async def main():
    conn = await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )
    try:
        # ── Current context ────────────────────────────────────────────────
        db = await conn.meta.get_current_database()
        schema = await conn.meta.get_current_schema()
        print(f"Connected to: {db}  (current schema: {schema})")

        # ── Schemas & databases ────────────────────────────────────────────
        schemas = await conn.meta.get_schemas()
        print(f"\nSchemas ({len(schemas)}):")
        for s in schemas:
            print(f"  {s}")

        dbs = await conn.meta.get_databases()
        print(f"\nDatabases ({len(dbs)}):")
        for d in dbs:
            print(f"  {d}")

        # ── Tables ─────────────────────────────────────────────────────────
        tables = await conn.meta.get_tables()
        print(f"\nUser tables ({len(tables)}):")
        for t in tables[:10]:
            print(f"  {t['schema']}.{t['table_name']}  ({t.get('row_count', '?')} rows)")

        # ── Views ──────────────────────────────────────────────────────────
        views = await conn.meta.get_views()
        print(f"\nViews ({len(views)}):")
        for v in views[:10]:
            print(f"  {v['schema']}.{v['view_name']}")

        # ── Columns (first table found) ────────────────────────────────────
        if tables:
            first = tables[0]
            print(f"\nColumns of {first['schema']}.{first['table_name']}:")
            cols = await conn.meta.get_columns(
                first["table_name"], schema=first["schema"]
            )
            for c in cols:
                print(f"  {c['column_name']:30s} {c['data_type']:20s} "
                      f"nullable={c['nullable']}")

        # ── Table sizes ────────────────────────────────────────────────────
        sizes = await conn.meta.get_table_sizes()
        print(f"\nTable sizes ({len(sizes)}):")
        for s in sizes[:10]:
            print(f"  {s['schema']}.{s['table_name']:30s} "
                  f"{s['size_mb']:>6} MB  skew={s.get('skew', 'N/A')}")

        # ── Distribution keys ──────────────────────────────────────────────
        print("\nDistribution keys (first 5 tables):")
        for t in tables[:5]:
            dk = await conn.meta.get_distribution_key(
                t["table_name"], schema=t["schema"]
            )
            print(f"  {t['schema']}.{t['table_name']}: "
                  f"{', '.join(dk) if dk else 'RANDOM'}")

        # ── Procedures ─────────────────────────────────────────────────────
        procs = await conn.meta.get_procedures()
        print(f"\nStored procedures ({len(procs)}):")
        for p in procs[:10]:
            print(f"  {p['schema']}.{p['proc_name']}  "
                  f"--> {p.get('returns', 'void')}")

        # ── Sequences ──────────────────────────────────────────────────────
        seqs = await conn.meta.get_sequences()
        print(f"\nSequences ({len(seqs)}):")
        for s in seqs:
            print(f"  {s['schema']}.{s['seq_name']}")

        # ── Synonyms ───────────────────────────────────────────────────────
        syns = await conn.meta.get_synonyms()
        print(f"\nSynonyms ({len(syns)}):")
        for s in syns:
            print(f"  {s['schema']}.{s['synonym_name']}  "
                  f"--> {s.get('ref_database', '')}.."
                  f"{s.get('ref_schema', '')}.{s.get('referenced_object', '')}")

        # ── Active sessions ────────────────────────────────────────────────
        sessions = await conn.meta.get_sessions()
        print(f"\nActive sessions ({len(sessions)}):")
        for s in sessions[:5]:
            print(f"  id={s['session_id']}  user={s['username']}  "
                  f"db={s['database_name']}  connected={s['conntime']}")

        # ── Users & groups ─────────────────────────────────────────────────
        users = await conn.meta.get_users()
        print(f"\nUsers ({len(users)}):")
        for u in users:
            print(f"  {u['username']}")

        groups = await conn.meta.get_groups()
        print(f"\nGroups ({len(groups)}):")
        for g in groups:
            print(f"  {g['groupname']}")

        # ── Search across object types ─────────────────────────────────────
        results = await conn.meta.search_objects("%")
        counts = {}
        for r in results:
            counts[r["object_type"]] = counts.get(r["object_type"], 0) + 1
        print(f"\nObject search summary:")
        for obj_type, count in sorted(counts.items()):
            print(f"  {obj_type}: {count}")

        # ── Query history (if enabled) ─────────────────────────────────────
        history = await conn.meta.get_query_history(limit=5)
        if history:
            print(f"\nRecent queries ({len(history)}):")
            for h in history:
                txt = (h.get('query_text', '') or '')[:80]
                print(f"  [{h.get('username', '?')}] {txt}...")
        else:
            print("\nQuery history: not enabled or empty")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

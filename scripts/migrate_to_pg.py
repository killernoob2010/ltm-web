"""
Migrate SQLite data -> Supabase PostgreSQL.
Run: DATABASE_URL='postgresql://...' python3 scripts/migrate_to_pg.py
"""
import os
import sys
import sqlite3
import psycopg2
import psycopg2.extras

SQLITE_DB = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'app.db')
PG_URL = os.getenv('DATABASE_URL')
if not PG_URL:
    print('ERROR: DATABASE_URL not set')
    sys.exit(1)

TABLES = [
    'users',
    'module_permissions',
    'user_sessions',
    'operation_logs',
    'alert_settings',
    'alert_history',
    'calculated_data',
    'daily_prices',
    'trading_days',
    'strategy_groups',
    'strategy_positions',
    'sh_junneng_trades',
]

def main():
    # Connect to SQLite
    print(f'Reading SQLite: {SQLITE_DB}')
    sqlite = sqlite3.connect(SQLITE_DB)
    sqlite.row_factory = sqlite3.Row

    # Connect to PG
    print(f'Connecting to PG...')
    pg = psycopg2.connect(PG_URL, connect_timeout=30)
    pg_cur = pg.cursor()

    # Disable FK checks during migration
    pg_cur.execute('SET session_replication_role = replica')
    pg.commit()

    stats = {}
    for table in TABLES:
        # Read from SQLite
        sqlite_cur = sqlite.cursor()
        sqlite_cur.execute(f'SELECT * FROM "{table}"')
        rows = sqlite_cur.fetchall()
        if not rows:
            print(f'  {table}: 0 rows, skipping')
            stats[table] = 0
            continue

        columns = [desc[0] for desc in sqlite_cur.description]
        col_str = ', '.join(f'"{c}"' for c in columns)
        placeholders = ', '.join(['%s'] * len(columns))

        # Clear target table
        pg_cur.execute(f'DELETE FROM "{table}"')

        # Insert
        data = [tuple(r[c] for c in columns) for r in rows]
        pg_cur.executemany(
            f'INSERT INTO "{table}" ({col_str}) VALUES ({placeholders})',
            data
        )
        pg.commit()

        # Reset SERIAL sequence
        try:
            pg_cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM \"{table}\"")
        except Exception as e:
            print(f'  {table}: sequence reset warning: {e}')

        print(f'  {table}: {len(rows)} rows migrated')
        stats[table] = len(rows)

    # Re-enable FK checks
    pg_cur.execute('SET session_replication_role = DEFAULT')
    pg.commit()

    sqlite.close()
    pg_cur.close()
    pg.close()

    print()
    print('=== Migration Summary ===')
    total = sum(stats.values())
    for table, count in stats.items():
        print(f'  {table}: {count}')
    print(f'  TOTAL: {total}')
    print('Done.')

if __name__ == '__main__':
    main()

import sqlite3, sys, os
sys.stdout.reconfigure(encoding='utf-8')

for commit in ['dce44aa', '5875192', '6c5085b']:
    os.system(f'git show {commit}:db.sqlite3 > temp_old_db.sqlite3')
    size = os.path.getsize('temp_old_db.sqlite3')
    print(f"\nCommit {commit}: size={size}")
    if size < 100:
        print("  Too small, likely empty")
        continue
    try:
        conn = sqlite3.connect('temp_old_db.sqlite3')
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        print(f"  Tables: {tables}")
        for t in tables:
            if 'database' in t.lower():
                c.execute(f"SELECT COUNT(*) FROM [{t}]")
                print(f"  {t}: {c.fetchone()[0]} records")
        conn.close()
    except Exception as e:
        print(f"  Error: {e}")

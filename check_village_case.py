"""Check if villages are stored in lowercase"""
from core.db import get_connection, release_connection
import psycopg2.extras

conn = get_connection()
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

try:
    # Check recently inserted locations
    cur.execute("""
        SELECT village, state, location_id, created_at
        FROM operations.locations 
        WHERE location_id LIKE 'L_503%'
        ORDER BY created_at DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    print('Sample recently inserted villages (should be lowercase):')
    print('-' * 80)
    all_lowercase = True
    for r in rows:
        is_lower = r['village'] == r['village'].lower() if r['village'] else True
        if not is_lower:
            all_lowercase = False
        status = '[OK]' if is_lower else '[NOT LOWERCASE]'
        print(f"{status} {r['village']} - {r['state']} (ID: {r['location_id']})")
    
    print('-' * 80)
    if all_lowercase:
        print('[SUCCESS] All villages are stored in lowercase!')
    else:
        print('[WARNING] Some villages are not in lowercase!')
    
    # Count total locations
    cur.execute("SELECT COUNT(*) as total FROM operations.locations")
    total = cur.fetchone()['total']
    print(f'\nTotal locations in table: {total}')
    
finally:
    cur.close()
    release_connection(conn)



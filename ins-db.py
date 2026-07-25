import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_db_connection

def view_recent_signals(limit=10):
    """Prints the most recently stored hiring signals directly from PostgreSQL."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    query = """
        SELECT 
            s.id, 
            p.name, 
            p.institution, 
            s.signal_type, 
            s.confidence_score, 
            s.raw_text, 
            s.source_url
        FROM hiring_signals s
        JOIN professors p ON s.professor_id = p.id
        ORDER BY s.id DESC
        LIMIT %s;
    """
    
    cur.execute(query, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\n🔍 --- LAST {len(rows)} SIGNALS STORED IN POSTGRESQL --- 🔍\n")
    if not rows:
        print("⚠️ No hiring signals found in the database.")
        return

    for row in rows:
        sig_id, name, inst, sig_type, conf, raw_text, url = row
        print(f"📌 Signal ID #{sig_id} | Prof: {name} ({inst})")
        print(f"   ├─ Type: {sig_type} | Confidence: {conf}")
        print(f"   ├─ Source: {url}")
        print(f"   └─ Raw Quote: \"{raw_text}\"")
        print("-" * 70)

if __name__ == "__main__":
    view_recent_signals()
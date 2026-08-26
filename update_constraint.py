import psycopg
import os
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get("DATABASE_URL")

with psycopg.connect(db_url) as conn:
    with conn.cursor() as cur:
        # Drop the existing check constraint
        cur.execute('ALTER TABLE saved_items DROP CONSTRAINT IF EXISTS saved_items_kind_check;')
        # Add the updated check constraint allowing the new favorite kinds
        cur.execute("ALTER TABLE saved_items ADD CONSTRAINT saved_items_kind_check CHECK (kind IN ('result', 'section', 'favorite_result', 'favorite_section'));")
    conn.commit()
print("Updated constraint successfully!")

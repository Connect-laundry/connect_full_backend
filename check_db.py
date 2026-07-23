import os
import psycopg
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

# Get DATABASE_URL or fallback
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    print("DATABASE_URL is not set in environment. Please set DATABASE_URL or add it to .env")
    exit(1)

conn = psycopg.connect(db_url)

with conn.cursor() as cursor:
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    tables = cursor.fetchall()
    print("Tables in database:")
    for table in tables:
        print(f"- {table[0]}")
    
    # Check payments_payment specifically
    print("\nColumns in payments_payment:")
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'payments_payment'")
    cols = cursor.fetchall()
    for col in cols:
        print(f"- {col[0]}")

conn.close()

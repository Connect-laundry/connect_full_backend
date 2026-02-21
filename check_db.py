import os
import psycopg
from urllib.parse import urlparse

# Get DATABASE_URL or fallback
db_url = os.environ.get('DATABASE_URL', 'postgresql://neondb_owner:npg_bZNvqDo6MC1i@ep-round-haze-afaxzo0c-pooler.c-2.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require')

result = urlparse(db_url)
username = result.username
password = result.password
database = result.path[1:]
hostname = result.hostname
port = result.port

conn = psycopg.connect(
    dbname=database,
    user=username,
    password=password,
    host=hostname,
    port=port
)

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

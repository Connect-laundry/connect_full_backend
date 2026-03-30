import psycopg
import uuid
import hashlib
from datetime import datetime, timedelta

db_url = 'postgresql://neondb_owner:npg_bZNvqDo6MC1i@ep-round-haze-afaxzo0c-pooler.c-2.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'


def create_token(email):
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # Find user
                cur.execute(
                    'SELECT id FROM users_user WHERE email = %s', (email,))
                user_row = cur.fetchone()
                if not user_row:
                    print('User not found')
                    return None
                user_id = user_row[0]

                # Generate raw token
                raw_token = str(uuid.uuid4())
                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                expires_at = datetime.now() + timedelta(hours=1)

                # Insert token
                cur.execute(
                    'INSERT INTO users_passwordresettoken (id, token_hash, expires_at, created_at, user_id) VALUES (%s, %s, %s, %s, %s)',
                    (str(
                        uuid.uuid4()),
                        token_hash,
                        expires_at,
                        datetime.now(),
                        user_id))
                print(f'TOKEN_RESULT:{raw_token}')
                return raw_token
    except Exception as e:
        print(f'Error: {e}')
        return None


def main():
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM users_user WHERE email LIKE 'test_reset_%' ORDER BY created_at DESC LIMIT 1")
                email_row = cur.fetchone()
                if email_row:
                    email = email_row[0]
                    print(f'Testing with email: {email}')
                    create_token(email)
                else:
                    print('No test user found')
    except Exception as e:
        print(f'Error searching user: {e}')


if __name__ == '__main__':
    main()

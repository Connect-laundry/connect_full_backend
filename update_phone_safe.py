import os
import re
import uuid

def process_file(filepath):
    if 'test_booking_creation.py' in filepath or 'conftest.py' in filepath:
        return

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    def replacer(match):
        phone = '+233' + str(uuid.uuid4().int)[:9]
        return f'create_user(phone="{phone}", email='

    new_content, count = re.subn(r'create_user\(email=', replacer, content)

    if count > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated {count} calls in {filepath}')

for root, _, files in os.walk('c:\\projects\\CONNECT\\connect_new_backend'):
    if 'venv' in root or '.venv' in root or '.git' in root:
        continue
    for file in files:
        if file.endswith('.py'):
            try:
                process_file(os.path.join(root, file))
            except Exception as e:
                print(f"Failed to process {os.path.join(root, file)}: {e}")

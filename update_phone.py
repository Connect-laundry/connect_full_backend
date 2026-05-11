import os
import re
import uuid

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    def replacer(match):
        phone = '+1234' + str(uuid.uuid4().int)[:10]
        # match.group(1) is the first captured group which is "email=" etc.
        return f'create_user(phone="{phone}", {match.group(1)}'

    new_content, count = re.subn(r'create_user\(\s*(?!phone=)(email=)', replacer, content)

    if count > 0:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f'Updated {count} calls in {filepath}')

for root, _, files in os.walk('c:\\projects\\CONNECT\\connect_new_backend'):
    for file in files:
        if file.endswith('.py'):
            process_file(os.path.join(root, file))

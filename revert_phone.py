import os
import re

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content, count = re.subn(r'phone="\+1234\d+",\s*', '', content)

    if count > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Reverted {count} calls in {filepath}')

for root, _, files in os.walk('c:\\projects\\CONNECT\\connect_new_backend'):
    for file in files:
        if file.endswith('.py'):
            try:
                process_file(os.path.join(root, file))
            except Exception as e:
                pass

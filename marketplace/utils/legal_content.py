"""Legal CMS content rendering and DOCX extraction helpers.

The backend intentionally avoids frontend-hardcoded legal copy. Markdown is the
stored authoring format, and HTML is generated server-side from a small,
sanitized subset that is safe for mobile, web, and public legal pages.
"""
import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


_W_NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def _inline_markdown_to_html(text):
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    escaped = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', escaped)
    escaped = re.sub(
        r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
        r'<a href="\2" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    return escaped


def render_markdown_to_safe_html(markdown_text):
    """Render a conservative Markdown subset to safe HTML.

    Supported blocks: headings, paragraphs, ordered/unordered lists, blockquotes,
    horizontal rules, and simple pipe tables. Raw HTML is escaped by default.
    """
    lines = (markdown_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    html_parts = []
    list_stack = []
    table_rows = []

    def close_lists():
        while list_stack:
            html_parts.append(f'</{list_stack.pop()}>')

    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        html_parts.append('<table><tbody>')
        for row in table_rows:
            cells = [cell.strip() for cell in row.strip().strip('|').split('|')]
            html_parts.append(
                '<tr>' + ''.join(f'<td>{_inline_markdown_to_html(cell)}</td>' for cell in cells) + '</tr>'
            )
        html_parts.append('</tbody></table>')
        table_rows = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_table()
            close_lists()
            continue

        if stripped.startswith('|') and stripped.endswith('|'):
            divider = set(stripped.replace('|', '').replace('-', '').replace(':', '').strip())
            if not divider:
                continue
            close_lists()
            table_rows.append(stripped)
            continue

        flush_table()

        heading = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if heading:
            close_lists()
            level = len(heading.group(1))
            html_parts.append(f'<h{level}>{_inline_markdown_to_html(heading.group(2))}</h{level}>')
            continue

        if stripped in {'---', '***', '___'}:
            close_lists()
            html_parts.append('<hr>')
            continue

        quote = re.match(r'^>\s+(.+)$', stripped)
        if quote:
            close_lists()
            html_parts.append(f'<blockquote>{_inline_markdown_to_html(quote.group(1))}</blockquote>')
            continue

        unordered = re.match(r'^[-*]\s+(.+)$', stripped)
        if unordered:
            if list_stack[-1:] != ['ul']:
                close_lists()
                html_parts.append('<ul>')
                list_stack.append('ul')
            html_parts.append(f'<li>{_inline_markdown_to_html(unordered.group(1))}</li>')
            continue

        ordered = re.match(r'^\d+[.)]\s+(.+)$', stripped)
        if ordered:
            if list_stack[-1:] != ['ol']:
                close_lists()
                html_parts.append('<ol>')
                list_stack.append('ol')
            html_parts.append(f'<li>{_inline_markdown_to_html(ordered.group(1))}</li>')
            continue

        close_lists()
        html_parts.append(f'<p>{_inline_markdown_to_html(stripped)}</p>')

    flush_table()
    close_lists()
    return '\n'.join(html_parts)


def extract_docx_markdown(path):
    """Extract readable Markdown from a DOCX file without third-party packages."""
    path = Path(path)
    with zipfile.ZipFile(path) as docx:
        xml = docx.read('word/document.xml')
    root = ElementTree.fromstring(xml)
    body = root.find('w:body', _W_NS)
    if body is None:
        return ''

    blocks = []
    for child in body:
        tag = child.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            text = _paragraph_to_markdown(child)
            if text:
                blocks.append(text)
        elif tag == 'tbl':
            table = _table_to_markdown(child)
            if table:
                blocks.append(table)
    return '\n\n'.join(blocks).strip()


def _paragraph_to_markdown(paragraph):
    texts = []
    for run in paragraph.findall('.//w:r', _W_NS):
        text = ''.join(t.text or '' for t in run.findall('.//w:t', _W_NS))
        if not text:
            continue
        run_props = run.find('w:rPr', _W_NS)
        if run_props is not None:
            if run_props.find('w:b', _W_NS) is not None:
                text = f'**{text}**'
            if run_props.find('w:i', _W_NS) is not None:
                text = f'*{text}*'
        texts.append(text)

    text = ''.join(texts).strip()
    if not text:
        return ''

    style = paragraph.find('w:pPr/w:pStyle', _W_NS)
    style_value = style.attrib.get(f'{{{_W_NS["w"]}}}val', '').lower() if style is not None else ''
    heading_match = re.search(r'heading([1-6])', style_value)
    if heading_match:
        return f'{"#" * int(heading_match.group(1))} {text}'

    if paragraph.find('w:pPr/w:numPr', _W_NS) is not None:
        return f'- {text}'

    return text


def _table_to_markdown(table):
    rows = []
    for row in table.findall('w:tr', _W_NS):
        cells = []
        for cell in row.findall('w:tc', _W_NS):
            parts = []
            for paragraph in cell.findall('w:p', _W_NS):
                text = _paragraph_to_markdown(paragraph)
                if text:
                    parts.append(text.replace('\n', ' '))
            cells.append(' '.join(parts).strip())
        if cells:
            rows.append(cells)
    if not rows:
        return ''

    width = max(len(row) for row in rows)
    padded = [row + [''] * (width - len(row)) for row in rows]
    markdown = ['| ' + ' | '.join(padded[0]) + ' |']
    markdown.append('| ' + ' | '.join(['---'] * width) + ' |')
    for row in padded[1:]:
        markdown.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(markdown)

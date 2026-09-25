"""Unit tests for the deterministic layers of price-list import.

Money parsing, the OCR text parser, validation, cross-check, matching, image
hardening (file-upload red team) and the circuit breaker. No network.
"""
import io
import struct
import zlib
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from laundries.services.price_import import circuit
from laundries.services.price_import.crosscheck import crosscheck
from laundries.services.price_import.errors import ImageRejected, ProviderErrorKind
from laundries.services.price_import.image import prepare_image, sanitize_filename
from laundries.services.price_import.matching import match_candidates, name_tokens
from laundries.services.price_import.money import detect_currency, find_amounts, parse_amount
from laundries.services.price_import.text_parser import parse_text
from laundries.services.price_import.validation import validate_extraction

from .price_import_helpers import extraction, image_bytes, item


# --- money ------------------------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('15', Decimal('15.00')),
    ('15.00', Decimal('15.00')),         # never 1500
    ('15.5', Decimal('15.50')),
    ('GH₵ 15', Decimal('15.00')),
    ('GH¢15', Decimal('15.00')),
    ('GHS 15.00', Decimal('15.00')),
    ('GHC 20', Decimal('20.00')),
    ('₵25', Decimal('25.00')),
    ('Cedis 15', Decimal('15.00')),
    ('15 cedis', Decimal('15.00')),
    ('1,500', Decimal('1500.00')),       # thousands separator
    ('1,500.00', Decimal('1500.00')),
    ('18/kg', Decimal('18.00')),
    ('15/-', Decimal('15.00')),          # Ghanaian notation
    ('GH\ufffd 15', Decimal('15.00')),   # OCR.space mangled cedi sign
])
def test_parse_amount_values(text, expected):
    assert parse_amount(text).value == expected


def test_decimal_comma_is_flagged_not_silently_trusted():
    parsed = parse_amount('15,50')
    assert parsed.value == Decimal('15.50')
    assert 'AMBIGUOUS_DECIMAL_SEPARATOR' in parsed.warnings


@pytest.mark.parametrize('text,warning', [
    ('15-20', 'PRICE_RANGE'),
    ('10 or 15', 'MULTIPLE_AMOUNTS'),
    ('-15', 'NEGATIVE_PRICE'),
    ('GH¢ -15', 'NEGATIVE_PRICE'),
    ('ask', 'PRICE_UNREADABLE'),
    ('123456789012', 'PRICE_UNREADABLE'),
])
def test_parse_amount_refuses_to_guess(text, warning):
    parsed = parse_amount(text)
    assert parsed.value is None
    assert warning in parsed.warnings


def test_currency_detection_needs_evidence():
    assert detect_currency('Shirt 15') is None          # bare number is not GHS
    assert detect_currency('Shirt GH¢ 15') == 'GHS'
    assert detect_currency('Shirt $15') == 'FOREIGN'


def test_find_amounts():
    assert find_amounts('Shirt\t10\t15\t20') == [Decimal('10.00'), Decimal('15.00'), Decimal('20.00')]


# --- deterministic OCR text parser -----------------------------------------------

def test_parser_dot_leaders_and_per_kg():
    ext = parse_text('FRESHCARE - PRICE LIST\nShirt ........ GH\ufffd 15\nTrouser .. GH\ufffd 20\nWash & Fold 18/kg')
    rows = {(i.raw_name, i.pricing_method): i for i in ext.items}
    assert rows[('Shirt', 'PER_ITEM')].price == '15'
    assert rows[('Trouser', 'PER_ITEM')].price == '20'
    kg = rows[('Wash & Fold', 'PER_KG')]
    assert kg.price_per_kg == '18' and kg.price is None
    assert ext.currency == 'GHS'
    assert all('PARSED_FROM_OCR_TEXT' in i.warnings for i in ext.items)


def test_parser_table_with_service_headers_creates_variants():
    text = 'SHIRTS\n\tWash & Fold\tWash & Iron\tDry Clean\nShirt\t10\t15\t20\n'
    ext = parse_text(text)
    variants = {(i.variant, i.price) for i in ext.items}
    assert variants == {('Wash & Fold', '10'), ('Wash & Iron', '15'), ('Dry Clean', '20')}
    assert all(i.category == 'Shirts' for i in ext.items)


def test_parser_multiple_prices_without_header_is_ambiguous_not_guessed():
    ext = parse_text('Shirt\t10\t15\t20')
    assert len(ext.items) == 3
    assert all('AMBIGUOUS_PAIRING' in i.warnings and i.pricing_method == 'UNKNOWN' for i in ext.items)


def test_parser_multi_column_line_pairs_name_and_price_in_order():
    # OCR.space returns one line per visual row of a multi-column list.
    ext = parse_text('Shirt ........ GH� 12\tTowel ........ GH� 8')
    assert [(i.raw_name, i.price) for i in ext.items] == [('Shirt', '12'), ('Towel', '8')]
    assert not any('AMBIGUOUS_PAIRING' in i.warnings for i in ext.items)


@pytest.mark.parametrize('text', ['GH$28', 'GHф28', 'GH₫28', 'GH� 28', 'GHS28', 'GH₵ 28'])
def test_ocr_mangled_cedi_signs_are_cedis_not_foreign(text):
    # Real OCR.space output reads the cedi sign as $, ф, ₫ or U+FFFD after "GH".
    parsed = parse_amount(text)
    assert parsed.value == Decimal('28.00') and parsed.currency_marker and not parsed.foreign_currency
    assert detect_currency(text) == 'GHS'


def test_parser_inline_pairs_from_real_engine2_multicolumn_output():
    text = ('KUMASI QUALITY CLEANERS\t\r\nPRICE LIST\t\r\n'
            'Kaftan......\tGH� 22 Bath Robe\tGH$ 21 Jeans\t...\tGH$ 18\t\r\n'
            'Suit (2 piece) .... GH$ 40 Tie ... GH$ 5\r\n')
    ext = parse_text(text)
    assert [(i.raw_name, i.price) for i in ext.items] == [
        ('Kaftan', '22'), ('Bath Robe', '21'), ('Jeans', '18'), ('Suit (2 piece)', '40'), ('Tie', '5')]
    assert ext.currency == 'GHS' and all(i.category is None for i in ext.items)


def test_parser_table_with_mangled_cedi_cells():
    text = 'Iron Only\tWash & Iron\t\r\nSuit (2 piece)\tGH$28\tGHф47\t\r\nJacket\tGH$19\tGH$29\t\r\n'
    rows = [(i.raw_name, i.variant, i.price) for i in parse_text(text).items]
    assert rows == [('Suit (2 piece)', 'Iron Only', '28'), ('Suit (2 piece)', 'Wash & Iron', '47'),
                    ('Jacket', 'Iron Only', '19'), ('Jacket', 'Wash & Iron', '29')]


def test_parser_pairs_name_with_price_on_the_next_line():
    # Real OCR.space Engine 2 output for a dot-leader list (live E2E, washing fixture):
    # most rows came back as the name on one line and "GH¢ 10" on the next.
    text = ("AUNTIE ESI'S LAUNDRY\t\r\nWashing prices (we wash, you iron at home)\t\r\nWASHING\t\r\n"
            "Shirt\t\r\nGH� 10\t\r\nTrouser\t\r\nGH� 15\t\r\nBedsheet (double)\tGH� 20\t\r\n"
            "Towel\t\r\nGH� 7\t\r\nALSO AVAILABLE\t\r\nShirt ironing\tGH� 8\t\r\n"
            "Prices in Ghana cedis. Thank you!\t\r\n")
    ext = parse_text(text)
    assert [(i.raw_name, i.category, i.price) for i in ext.items] == [
        ('Shirt', 'Washing', '10'), ('Trouser', 'Washing', '15'), ('Bedsheet (double)', 'Washing', '20'),
        ('Towel', 'Washing', '7'), ('Shirt ironing', 'Also Available', '8'),
    ]
    assert ext.warnings == []
    assert ext.items[0].source_text == 'Shirt GH� 10'


@pytest.mark.parametrize('text', [
    'Shirt\n\nWASHING\nGH¢ 10',                       # a heading in between
    'Shirt\nTrouser 15\nGH¢ 10',                      # another row in between
    'We wash and iron all your clothes today\nGH¢ 10',  # a sentence, not a name
    'Shirt\nGH¢ 10 GH¢ 12',                            # two amounts: which one?
])
def test_parser_never_pairs_a_price_with_a_non_adjacent_or_unlikely_name(text):
    ext = parse_text(text)
    assert not any(i.price == '10' and i.raw_name.lower().startswith(('shirt', 'we wash')) for i in ext.items)


def test_parser_one_kg_equals():
    ext = parse_text('1kg = 18')
    assert ext.items[0].pricing_method == 'PER_KG' and ext.items[0].price_per_kg == '18'


# --- validation -------------------------------------------------------------

def _validate(items, **kw):
    return validate_extraction(extraction(items, **kw), trusted_source=True)


def test_clean_row_looks_good():
    cands, doc, currency = _validate([item('Shirt', '15')])
    assert currency == 'GHS' and cands[0].review_state == 'LOOKS_GOOD'
    assert cands[0].price == Decimal('15.00') and doc == []


def test_null_price_is_unreadable_not_invented():
    cands, _, _ = _validate([item('Shirt', None, source='Shirt ....')])
    assert cands[0].price is None and cands[0].review_state == 'UNREADABLE'
    assert 'MISSING_PRICE' in cands[0].warnings


def test_negative_price_is_nulled():
    cands, _, _ = _validate([item('Shirt', '-15', source='Shirt -15')])
    assert cands[0].price is None and 'NEGATIVE_PRICE' in cands[0].warnings


def test_large_price_flagged():
    cands, _, _ = _validate([item('Wedding gown', '9000')])
    assert 'SUSPICIOUS_PRICE' in cands[0].warnings and cands[0].review_state == 'CHECK'


def test_price_missing_from_evidence_is_flagged():
    # Anti-hallucination: the model says 15 but its own evidence says 75.
    cands, _, _ = _validate([item('Shirt', '15', source='Shirt .... 75')])
    assert 'PRICE_NOT_IN_SOURCE_TEXT' in cands[0].warnings and cands[0].review_state == 'CHECK'


def test_unknown_pricing_method_needs_owner():
    cands, _, _ = _validate([item('Duvet', '60', method='UNKNOWN')])
    assert cands[0].review_state == 'CHECK' and 'PRICING_METHOD_UNKNOWN' in cands[0].warnings


def test_per_kg_amount_in_wrong_field_is_moved_and_flagged():
    row = item('Wash & Fold', '18', method='PER_ITEM', source='Wash & Fold 18/kg')
    row['pricing_method'] = 'PER_KG'
    cands, _, _ = _validate([row])
    assert cands[0].price_per_kg == Decimal('18.00') and cands[0].price is None
    assert 'PRICE_FIELD_MOVED' in cands[0].warnings


def test_per_item_with_only_kg_price_becomes_unknown():
    row = item('Shirt', '18', method='PER_KG')
    row['pricing_method'] = 'PER_ITEM'
    cands, _, _ = _validate([row])
    assert cands[0].pricing_method == 'UNKNOWN' and 'PRICING_METHOD_CONFLICT' in cands[0].warnings


def test_multiple_per_kg_rates_flagged():
    cands, _, _ = _validate([item('Wash & Fold', '18', method='PER_KG', source='Wash & Fold 18/kg'),
                             item('Wash & Iron', '25', method='PER_KG', source='Wash & Iron 25/kg')])
    assert all('MULTIPLE_PER_KG_RATES' in c.warnings for c in cands)


def test_currency_uncertain_blocks_looks_good():
    cands, doc, currency = _validate([item('Shirt', '15', source='Shirt 15')], currency=None)
    assert currency == '' and 'CURRENCY_UNCONFIRMED' in doc
    assert cands[0].review_state == 'CHECK'


def test_duplicate_rows():
    cands, _, _ = _validate([item('Shirt', '15'), item('Shirt', '15'), item('Trouser', '20'), item('Trouser', '25')])
    assert cands[1].is_selected is False and 'DUPLICATE_ROW' in cands[1].warnings
    assert 'CONFLICTING_DUPLICATE' in cands[2].warnings and 'CONFLICTING_DUPLICATE' in cands[3].warnings


def test_blank_name_unreadable():
    cands, _, _ = _validate([item('', '15', normalized_name=None, source='.... 15')])
    assert cands[0].review_state == 'UNREADABLE' and 'MISSING_NAME' in cands[0].warnings


def test_variants_become_distinct_names():
    cands, _, _ = _validate([
        item('Shirt', '10', variant='Wash & Fold', source='Shirt 10 15 20'),
        item('Shirt', '15', variant='Wash & Iron', source='Shirt 10 15 20'),
        item('Shirt', '20', variant='Dry Clean', source='Shirt 10 15 20'),
    ])
    assert [c.item_name for c in cands] == ['Shirt – Wash & Fold', 'Shirt – Wash & Iron', 'Shirt – Dry Clean']
    assert all(c.is_selected for c in cands)


def test_markup_and_control_chars_stripped_from_names():
    cands, _, _ = _validate([item('<b>Shirt</b>\x00', '15', normalized_name='<b>Shirt</b>')])
    assert '<' not in cands[0].item_name and '\x00' not in cands[0].raw_name


# --- cross-check ------------------------------------------------------------------

def test_crosscheck_disagreement_example_from_spec():
    cands, _, currency = _validate([item('Shirt', '15')])
    summary = crosscheck(cands, 'Shirt .... GH\ufffd 75', currency)
    assert 'PRICE_PROVIDER_DISAGREEMENT' in cands[0].warnings
    assert summary['disagreed'] == 1


def test_crosscheck_agreement_and_extra_price():
    cands, _, currency = _validate([item('Shirt', '15')])
    summary = crosscheck(cands, 'Shirt .... GH\ufffd 15\nSuit .... GH\ufffd 90', currency)
    assert summary['agreed'] == 1 and 'EXTRA_PRICE' in summary['doc_warnings']
    assert cands[0].warnings == []


def test_crosscheck_currency_mismatch():
    cands, _, currency = _validate([item('Shirt', '15', source='Shirt 15')], currency=None)
    summary = crosscheck(cands, 'Shirt GH\ufffd 15', currency)
    assert 'CURRENCY_MISMATCH' in summary['doc_warnings']


# --- matching ---------------------------------------------------------------

class _Existing:
    def __init__(self, name, price='15.00'):
        import uuid
        self.id, self.item_name, self.unit_price = uuid.uuid4(), name, Decimal(price)


def test_name_tokens_equivalence():
    assert name_tokens('Shirt Wash and Iron') == name_tokens('Wash & Iron Shirt')


def test_matching_exact_and_possible():
    cands, _, _ = _validate([item('Shirt', '15'), item('Wash & Iron Shirt', '18'), item('Kaftan', '30')])
    match_candidates(cands, [_Existing('shirt'), _Existing('Shirt Wash and Iron')])
    assert cands[0].match_type == 'EXACT'
    assert cands[1].match_type == 'POSSIBLE' and cands[1].matched_item_name == 'Shirt Wash and Iron'
    assert cands[2].match_type == 'NONE'
    assert cands[0].review_state == 'CHECK'


# --- image hardening / file-upload red team ------------------------------------

def _up(data, name='x.jpg', ctype='image/jpeg'):
    return SimpleUploadedFile(name, data, content_type=ctype)


def test_valid_jpeg_png_webp_are_normalised():
    for fmt, ctype in (('JPEG', 'image/jpeg'), ('PNG', 'image/png'), ('WEBP', 'image/webp')):
        prepared = prepare_image(_up(image_bytes(fmt), f'a.{fmt.lower()}', ctype))
        assert prepared.provider_bytes[:3] == b'\xff\xd8\xff'
        assert prepared.storage_name.endswith('.jpg') and len(prepared.sha256) == 64
        assert prepared.ocr_bytes and len(prepared.ocr_bytes) <= 950 * 1024


def test_exif_is_stripped_and_orientation_applied():
    img = Image.new('RGB', (400, 200), 'white')
    exif = Image.Exif()
    exif[0x0112] = 6                      # rotate 90
    exif[0x8825] = {2: (5.0, 36.0, 0.0)}  # GPS latitude
    exif[0x010F] = 'SecretCam'
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif)
    prepared = prepare_image(_up(buf.getvalue()))
    out = Image.open(io.BytesIO(prepared.provider_bytes))
    assert out.size == (200, 400)                         # orientation applied
    assert not out.getexif() and b'SecretCam' not in prepared.provider_bytes


def test_large_image_is_downscaled_never_upscaled():
    big = prepare_image(_up(image_bytes('JPEG', size=(5000, 3000))))
    assert max(big.width, big.height) == 2560
    small = prepare_image(_up(image_bytes('JPEG', size=(600, 400))))
    assert (small.width, small.height) == (600, 400)


def test_same_image_same_hash():
    data = image_bytes('PNG')
    assert prepare_image(_up(data, 'a.png', 'image/png')).sha256 == prepare_image(_up(data, 'b.png', 'image/png')).sha256


def _png_header_only(width, height):
    def chunk(tag, body):
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(b'\x00' * 10)) + chunk(b'IEND', b'')


@pytest.mark.parametrize('data,name,ctype,code', [
    (b'', 'empty.jpg', 'image/jpeg', 'EMPTY_FILE'),
    (b'MZ\x90\x00' + b'\x00' * 200, 'evil.exe.jpg', 'image/jpeg', 'UNSUPPORTED_FILE'),       # renamed EXE
    (b'<html><script>alert(1)</script></html>', 'page.png', 'image/png', 'UNSUPPORTED_FILE'),  # HTML as PNG
    (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', 'x.svg', 'image/svg+xml', 'UNSUPPORTED_FILE'),
    (b'<?xml version="1.0"?><svg/>', 'x.png', 'image/png', 'UNSUPPORTED_FILE'),
    (b'PK\x03\x04' + b'\x00' * 100, 'archive.jpg', 'image/jpeg', 'UNSUPPORTED_FILE'),         # ZIP
    (b'%PDF-1.7\n' + b'\x00' * 100, 'list.pdf', 'application/pdf', 'UNSUPPORTED_FILE'),
    (b'\x00\x00\x00\x18ftypheic' + b'\x00' * 100, 'IMG_1.heic', 'image/heic', 'UNSUPPORTED_FILE'),
])
def test_rejects_non_images(data, name, ctype, code):
    with pytest.raises(ImageRejected) as exc:
        prepare_image(_up(data, name, ctype))
    assert exc.value.code == code


def test_rejects_truncated_jpeg():
    data = image_bytes('JPEG')
    with pytest.raises(ImageRejected) as exc:
        prepare_image(_up(data[: len(data) // 2]))
    assert exc.value.code == 'INVALID_IMAGE'


def test_rejects_decompression_bomb_before_decoding():
    with pytest.raises(ImageRejected) as exc:
        prepare_image(_up(_png_header_only(100_000, 100_000), 'bomb.png', 'image/png'))
    assert exc.value.code == 'IMAGE_DIMENSIONS_TOO_LARGE'


def test_rejects_animated_webp():
    frames = [Image.new('RGB', (200, 200), c) for c in ('red', 'blue')]
    buf = io.BytesIO()
    frames[0].save(buf, format='WEBP', save_all=True, append_images=frames[1:])
    with pytest.raises(ImageRejected):
        prepare_image(_up(buf.getvalue(), 'a.webp', 'image/webp'))


def test_rejects_gif():
    buf = io.BytesIO()
    Image.new('RGB', (200, 200)).save(buf, format='GIF')
    with pytest.raises(ImageRejected):
        prepare_image(_up(buf.getvalue(), 'a.gif', 'image/gif'))


def test_rejects_mismatched_declared_type():
    with pytest.raises(ImageRejected):
        prepare_image(_up(image_bytes('JPEG'), 'a.jpg', 'text/html'))


def test_rejects_polyglot_jpeg_with_html_tail_is_reencoded():
    # A valid JPEG followed by an HTML payload: accepted as an image, but the
    # re-encode means the payload is gone from what we store/send.
    data = image_bytes('JPEG') + b'<script>alert(1)</script>'
    prepared = prepare_image(_up(data))
    assert b'<script>' not in prepared.provider_bytes


@override_settings(PRICE_LIST_UPLOAD_MAX_MB=1)
def test_rejects_oversized_bytes():
    with pytest.raises(ImageRejected) as exc:
        prepare_image(_up(b'\xff\xd8\xff' + b'\x00' * (1024 * 1024 + 10)))
    assert exc.value.code == 'FILE_TOO_LARGE' and exc.value.http_status == 413


def test_tiny_image_rejected():
    with pytest.raises(ImageRejected) as exc:
        prepare_image(_up(image_bytes('PNG', size=(8, 8), text=False), 'a.png', 'image/png'))
    assert exc.value.code == 'IMAGE_TOO_SMALL'


@pytest.mark.parametrize('raw,expected', [
    ('../../etc/passwd.jpg', 'passwd.jpg'),
    ('C:\\Windows\\system32\\x.png', 'x.png'),
    ('prix-liste-été.jpg', 'prix-liste-été.jpg'),
    ('a\x00b<script>.jpg', 'ab_script_.jpg'),
    ('menu.php.jpg', 'menu.php.jpg'),          # display only; never used as a path
])
def test_filename_sanitised(raw, expected):
    assert sanitize_filename(raw) == expected


# --- circuit breaker --------------------------------------------------------------

def test_circuit_opens_after_repeated_unavailable_and_closes_on_success():
    for _ in range(3):
        circuit.record_failure('gemini', ProviderErrorKind.UNAVAILABLE)
    assert circuit.is_open('gemini')
    circuit.record_success('gemini')
    assert not circuit.is_open('gemini')


def test_auth_failure_opens_circuit_immediately():
    circuit.record_failure('gemini', ProviderErrorKind.AUTH)
    assert circuit.is_open('gemini')


def test_invalid_input_does_not_trip_circuit():
    for _ in range(5):
        circuit.record_failure('gemini', ProviderErrorKind.INVALID_INPUT)
    assert not circuit.is_open('gemini')


@override_settings(OCR_SPACE_MONTHLY_LIMIT=2)
def test_ocr_monthly_budget():
    assert circuit.ocr_quota_available()
    circuit.record_ocr_call()
    circuit.record_ocr_call()
    assert not circuit.ocr_quota_available()

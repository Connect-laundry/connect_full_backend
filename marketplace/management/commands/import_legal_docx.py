from pathlib import Path

# pyre-ignore[missing-module]
from django.core.management.base import BaseCommand, CommandError

from marketplace.services.legal import save_imported_legal_page
from marketplace.utils.legal_content import extract_docx_markdown


DEFAULT_DOCS = {
    'CONNECTLAUNDRY PRIVACY POLICY.docx': {
        'title': 'Connect Laundry Privacy Policy',
        'slug': 'privacy-policy',
        'document_type': 'PRIVACY_POLICY',
    },
    'CONNECTLAUNDRY TERMS OF SERVICE.docx': {
        'title': 'Connect Laundry Terms of Service',
        'slug': 'terms-of-service',
        'document_type': 'TERMS_OF_SERVICE',
    },
    'CONNECTLAUNDRY NDA.docx': {
        'title': 'Connect Laundry NDA',
        'slug': 'nda',
        'document_type': 'NDA',
    },
    'CONNECTLAUNDRY FOUNDERS AGREEMENT.docx': {
        'title': 'Connect Laundry Founders Agreement',
        'slug': 'founders-agreement',
        'document_type': 'FOUNDERS_AGREEMENT',
    },
    'CONNECTLAUNDRY VENDOR PARTNER AGREEMENT.docx': {
        'title': 'Connect Laundry Vendor Partner Agreement',
        'slug': 'vendor-partner-agreement',
        'document_type': 'VENDOR_PARTNER_AGREEMENT',
    },
}


class Command(BaseCommand):
    help = 'Import Connect Laundry legal DOCX files into the versioned Legal CMS.'

    def add_arguments(self, parser):
        parser.add_argument(
            'source',
            nargs='?',
            default='CONNECT-LAUNDRY-LEGAL',
            help='Directory containing DOCX files or a single DOCX file path.',
        )
        parser.add_argument(
            '--draft',
            action='store_true',
            help='Import as drafts instead of publishing immediately.',
        )
        parser.add_argument(
            '--title',
            default='',
            help='Title for a single custom DOCX import.',
        )
        parser.add_argument(
            '--slug',
            default='',
            help='Slug for a single custom DOCX import.',
        )
        parser.add_argument(
            '--document-type',
            default='',
            help='Document type for a single custom DOCX import.',
        )

    def handle(self, *args, **options):
        source = Path(options['source'])
        if not source.is_absolute():
            source = Path.cwd() / source
            if not source.exists():
                source = Path(__file__).resolve().parents[3] / options['source']
        if not source.exists():
            raise CommandError(f'Source not found: {source}')

        paths = [source] if source.is_file() else sorted(source.glob('*.docx'))
        if not paths:
            raise CommandError(f'No DOCX files found in {source}')

        imported = 0
        for path in paths:
            meta = DEFAULT_DOCS.get(path.name, {})
            title = options['title'] or meta.get('title') or path.stem.replace('-', ' ').title()
            slug = options['slug'] or meta.get('slug') or path.stem
            document_type = options['document_type'] or meta.get('document_type') or path.stem
            markdown = extract_docx_markdown(path)
            if not markdown:
                self.stderr.write(self.style.WARNING(f'Skipped empty DOCX: {path.name}'))
                continue

            page, created = save_imported_legal_page(
                title=title,
                slug=slug,
                document_type=document_type,
                content_markdown=markdown,
                publish=not options['draft'],
                change_log=f'Imported from {path.name}.',
            )
            state = 'created' if created else 'versioned'
            imported += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'{state}: {page.title} ({page.slug}) v{page.version_number}'
                )
            )

        self.stdout.write(self.style.SUCCESS(f'Imported {imported} legal document(s).'))

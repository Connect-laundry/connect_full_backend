# Generated for the Connect Laundry Legal CMS.

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from django.utils.html import escape
from django.utils.text import slugify


def populate_legal_pages(apps, schema_editor):
    LegalPage = apps.get_model('marketplace', 'LegalPage')
    legacy = {
        'TOS': ('terms-of-service', 'TERMS_OF_SERVICE'),
        'PRIVACY': ('privacy-policy', 'PRIVACY_POLICY'),
        'ABOUT': ('about-us', 'ABOUT_US'),
    }
    for page in LegalPage.objects.all():
        slug, document_type = legacy.get(
            page.document_type,
            (slugify(page.title or page.document_type), page.document_type),
        )
        page.slug = slug
        page.document_type = document_type
        page.is_published = True
        page.is_public = True
        page.effective_date = page.published_at
        page.content_html = ''.join(
            f'<p>{escape(block.strip())}</p>'
            for block in (page.content_markdown or '').split('\n\n')
            if block.strip()
        )
        page.save(update_fields=[
            'slug', 'document_type', 'is_published', 'is_public',
            'effective_date', 'content_html',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0008_auditlog_notification_action_url_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name='LegalDocument',
            new_name='LegalPage',
        ),
        migrations.RenameField(
            model_name='legalpage',
            old_name='content',
            new_name='content_markdown',
        ),
        migrations.RenameField(
            model_name='legalpage',
            old_name='version',
            new_name='version_number',
        ),
        migrations.AddField(
            model_name='legalpage',
            name='slug',
            field=models.SlugField(db_index=True, default='', max_length=140),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='legalpage',
            name='short_description',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='content_html',
            field=models.TextField(blank=True, default='', editable=False, verbose_name='content html'),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='summary',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='effective_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='last_modified_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='legal_pages_modified', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='requires_user_reacceptance',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='is_published',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='is_public',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='seo_title',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='seo_description',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='previous_version',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='next_versions', to='marketplace.legalpage'),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='change_log',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='tags',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='language_code',
            field=models.CharField(db_index=True, default='en', max_length=10),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='legalpage',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='legalpage',
            name='content_markdown',
            field=models.TextField(verbose_name='content markdown'),
        ),
        migrations.AlterField(
            model_name='legalpage',
            name='document_type',
            field=models.CharField(db_index=True, max_length=80),
        ),
        migrations.AlterField(
            model_name='legalpage',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name='legalpage',
            options={'ordering': ['sort_order', 'title', '-published_at', '-updated_at'], 'verbose_name': 'Legal Page', 'verbose_name_plural': 'Legal Pages'},
        ),
        migrations.RunPython(populate_legal_pages, migrations.RunPython.noop),
        migrations.CreateModel(
            name='UserLegalAcceptance',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('accepted_version', models.CharField(max_length=20)),
                ('accepted_at', models.DateTimeField(auto_now_add=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True, default='')),
                ('platform', models.CharField(blank=True, default='', max_length=40)),
                ('app_version', models.CharField(blank=True, default='', max_length=40)),
                ('legal_page', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='acceptances', to='marketplace.legalpage')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='legal_acceptances', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User Legal Acceptance',
                'verbose_name_plural': 'User Legal Acceptances',
                'ordering': ['-accepted_at'],
            },
        ),
        migrations.CreateModel(
            name='LegalDocument',
            fields=[],
            options={
                'verbose_name': 'Legal Document',
                'verbose_name_plural': 'Legal Documents',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('marketplace.legalpage',),
        ),
        migrations.AddConstraint(
            model_name='legalpage',
            constraint=models.UniqueConstraint(fields=('slug', 'version_number', 'language_code'), name='unique_legal_page_slug_version_language'),
        ),
        migrations.AddIndex(
            model_name='legalpage',
            index=models.Index(fields=['slug', 'language_code', 'is_active', 'is_published'], name='marketplace_slug_18fb7f_idx'),
        ),
        migrations.AddIndex(
            model_name='legalpage',
            index=models.Index(fields=['document_type', 'is_published', 'is_active'], name='marketplace_documen_2a4752_idx'),
        ),
        migrations.AddIndex(
            model_name='legalpage',
            index=models.Index(fields=['published_at'], name='marketplace_publish_c79b73_idx'),
        ),
        migrations.AddIndex(
            model_name='legalpage',
            index=models.Index(fields=['effective_date'], name='marketplace_effecti_34e1b2_idx'),
        ),
        migrations.AddConstraint(
            model_name='userlegalacceptance',
            constraint=models.UniqueConstraint(fields=('user', 'legal_page', 'accepted_version'), name='unique_user_legal_acceptance_version'),
        ),
        migrations.AddIndex(
            model_name='userlegalacceptance',
            index=models.Index(fields=['user', 'accepted_at'], name='marketplace_user_id_5b9ed6_idx'),
        ),
        migrations.AddIndex(
            model_name='userlegalacceptance',
            index=models.Index(fields=['legal_page', 'accepted_version'], name='marketplace_legal_p_f12a51_idx'),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(choices=[('ADMIN_SEARCH', 'Admin Search'), ('USER_ROLE_CHANGED', 'User Role Changed'), ('USER_EDITED', 'User Edited'), ('LAUNDRY_APPROVED', 'Laundry Approved'), ('LAUNDRY_REJECTED', 'Laundry Rejected'), ('ORDER_STATUS_CHANGED', 'Order Status Changed'), ('PAYMENT_ACTION', 'Payment Action'), ('COUPON_CREATED', 'Coupon Created'), ('NOTIFICATION_DISMISSED', 'Notification Dismissed'), ('LEGAL_DOCUMENT_CREATED', 'Legal Document Created'), ('LEGAL_DOCUMENT_UPDATED', 'Legal Document Updated'), ('LEGAL_DOCUMENT_PUBLISHED', 'Legal Document Published'), ('LEGAL_DOCUMENT_ARCHIVED', 'Legal Document Archived'), ('LEGAL_DOCUMENT_ROLLED_BACK', 'Legal Document Rolled Back'), ('LEGAL_ACCEPTANCE_RECORDED', 'Legal Acceptance Recorded'), ('PERMISSION_DENIED', 'Permission Denied'), ('SECURITY_EVENT', 'Security Event')], db_index=True, max_length=40),
        ),
    ]

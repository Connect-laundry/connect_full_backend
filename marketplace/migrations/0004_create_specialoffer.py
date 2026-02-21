# pyre-ignore[missing-module]
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0003_rename_notification_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='SpecialOffer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=255, verbose_name='title')),
                ('description', models.TextField(blank=True, verbose_name='description')),
                ('image', models.ImageField(upload_to='special_offers/', verbose_name='image')),
                ('is_active', models.BooleanField(default=True)),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('order', models.PositiveIntegerField(default=0, help_text='Order in carousel')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Special Offer',
                'verbose_name_plural': 'Special Offers',
                'ordering': ['order', '-created_at'],
            },
        ),
    ]

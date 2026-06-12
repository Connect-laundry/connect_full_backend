# Generated for Clerk social identity integration on 2026-06-12

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_alter_user_avatar'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='phone',
            field=models.CharField(blank=True, db_index=True, max_length=20, null=True, unique=True, verbose_name='phone number'),
        ),
        migrations.AddField(
            model_name='user',
            name='clerk_user_id',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='user',
            name='social_provider',
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name='user',
            name='social_profile_image_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='user',
            name='last_social_login_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ordering", "0010_add_gps_coordinates"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="actual_weight",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6, null=True
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="estimated_weight",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6, null=True
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="price_per_kg_snapshot",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="pricing_method",
            field=models.CharField(
                choices=[("PER_ITEM", "Per Item"), ("PER_KG", "Per Kg")],
                default="PER_ITEM",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="estimated_price",
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        # Note: final_price will be handled by renaming total_amount in 0012
    ]

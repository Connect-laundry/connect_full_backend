from decimal import Decimal
from ordering.models import LaunderableItem
from laundries.models.category import Category
import os
import django

# Setup Django environment
import sys

sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def seed_booking_data():
    print("--- Seeding Booking Data ---")

    # 1. Create Service Types
    service_names = {
        "Wash Only": None,
        "Wash & Iron": "cf76367a-fb24-4233-b86d-8d90b38b2202",
        "Dry Clean": None,
        "Ironing Only": None
    }
    services = {}
    for name, s_id in service_names.items():
        if s_id:
            service, created = Category.objects.get_or_create(
                id=s_id, defaults={"name": name, "type": "SERVICE_TYPE"}
            )
        else:
            service, created = Category.objects.get_or_create(
                name=name, defaults={"type": "SERVICE_TYPE"}
            )
        
        if not created and service.type != "SERVICE_TYPE":
            service.type = "SERVICE_TYPE"
            service.save()
        services[name] = service
        print(f"Service: {name} ({'Created' if created else 'Updated'})")

    # 2. Create Item Categories
    item_cat_names = ["Clothing", "Formal Wear", "Bedding", "Household"]
    item_cats = {}
    for name in item_cat_names:
        cat, created = Category.objects.get_or_create(
            name=name, defaults={"type": "ITEM_CATEGORY"}
        )
        if not created and cat.type != "ITEM_CATEGORY":
            cat.type = "ITEM_CATEGORY"
            cat.save()
        item_cats[name] = cat
        print(f"Item Category: {name} ({'Created' if created else 'Updated'})")

    # 3. Create Launderable Items
    items_data = [
        {
            "id": "6164ce16-1a7f-4a9e-9abf-372b83d4b5c6",
            "name": "Shirt",
            "category": "Clothing",
            "price": "10.00",
            "services": ["Wash Only", "Wash & Iron", "Ironing Only"],
        },
        {
            "id": None,
            "name": "Suit (2-Piece)",
            "category": "Formal Wear",
            "price": "45.00",
            "services": ["Dry Clean"],
        },
        {
            "id": None,
            "name": "Evening Gown",
            "category": "Formal Wear",
            "price": "60.00",
            "services": ["Dry Clean"],
        },
        {
            "id": None,
            "name": "Bed Sheet",
            "category": "Bedding",
            "price": "15.00",
            "services": ["Wash Only", "Wash & Iron"],
        },
        {
            "id": None,
            "name": "Jeans",
            "category": "Clothing",
            "price": "12.00",
            "services": ["Wash Only", "Wash & Iron"],
        },
        {
            "id": None,
            "name": "Curtains (Pair)",
            "category": "Household",
            "price": "40.00",
            "services": ["Wash Only", "Dry Clean"],
        },
    ]

    for item_info in items_data:
        i_id = item_info.get("id")
        if i_id:
            item, created = LaunderableItem.objects.get_or_create(
                id=i_id,
                defaults={
                    "name": item_info["name"],
                    "base_price": Decimal(item_info["price"]),
                    "item_category": item_cats[item_info["category"]],
                },
            )
        else:
            item, created = LaunderableItem.objects.get_or_create(
                name=item_info["name"],
                defaults={
                    "base_price": Decimal(item_info["price"]),
                    "item_category": item_cats[item_info["category"]],
                },
            )

        item.save()
        print(f"Item: {item.name} ({'Created' if created else 'Updated'})")

    print(
        "\n[SUCCESS] Seeding complete! 12 services/categories and 6 catalog items are ready."
    )


if __name__ == "__main__":
    seed_booking_data()

from django.core.management.base import BaseCommand
from laundries.models.category import Category
from ordering.models import LaunderableItem
from decimal import Decimal

class Command(BaseCommand):
    help = 'Seeds initial booking categories and launderable items'

    def handle(self, *args, **options):
        self.stdout.write("--- Seeding Booking Data ---")
        
        # 1. Create Service Types
        service_names = ["Wash Only", "Wash & Iron", "Dry Clean", "Ironing Only"]
        services = {}
        for name in service_names:
            service, created = Category.objects.get_or_create(
                name=name,
                defaults={'type': 'SERVICE_TYPE'}
            )
            if not created and service.type != 'SERVICE_TYPE':
                service.type = 'SERVICE_TYPE'
                service.save()
            services[name] = service
            self.stdout.write(self.style.SUCCESS(f"Service: {name} ({'Created' if created else 'Updated'})"))

        # 2. Create Item Categories
        item_cat_names = ["Clothing", "Formal Wear", "Bedding", "Household"]
        item_cats = {}
        for name in item_cat_names:
            cat, created = Category.objects.get_or_create(
                name=name,
                defaults={'type': 'ITEM_CATEGORY'}
            )
            if not created and cat.type != 'ITEM_CATEGORY':
                cat.type = 'ITEM_CATEGORY'
                cat.save()
            item_cats[name] = cat
            self.stdout.write(self.style.SUCCESS(f"Item Category: {name} ({'Created' if created else 'Updated'})"))

        # 3. Create Launderable Items
        items_data = [
            {
                "name": "Shirt",
                "category": "Clothing",
                "price": "10.00",
                "services": ["Wash Only", "Wash & Iron", "Ironing Only"]
            },
            {
                "name": "Suit (2-Piece)",
                "category": "Formal Wear",
                "price": "45.00",
                "services": ["Dry Clean"]
            },
            {
                "name": "Evening Gown",
                "category": "Formal Wear",
                "price": "60.00",
                "services": ["Dry Clean"]
            },
            {
                "name": "Bed Sheet",
                "category": "Bedding",
                "price": "15.00",
                "services": ["Wash Only", "Wash & Iron"]
            },
            {
                "name": "Jeans",
                "category": "Clothing",
                "price": "12.00",
                "services": ["Wash Only", "Wash & Iron"]
            },
            {
                "name": "Curtains (Pair)",
                "category": "Household",
                "price": "40.00",
                "services": ["Wash Only", "Dry Clean"]
            }
        ]

        for item_info in items_data:
            item, created = LaunderableItem.objects.get_or_create(
                name=item_info["name"],
                defaults={
                    "base_price": Decimal(item_info["price"]),
                    "item_category": item_cats[item_info["category"]]
                }
            )
            
            # Add supported services
            for s_name in item_info["services"]:
                item.supported_services.add(services[s_name])
            
            item.save()
            self.stdout.write(f"Item: {item.name} ({'Created' if created else 'Updated'}) linked to {len(item_info['services'])} services.")

        self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Seeding complete!"))

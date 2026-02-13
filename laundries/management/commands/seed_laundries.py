import random
import uuid
# pyre-ignore[missing-module]
from django.core.management.base import BaseCommand
# pyre-ignore[missing-module]
from laundries.models import Category, Laundry, Service
# pyre-ignore[missing-module]
from users.models import User

class Command(BaseCommand):
    help = 'Seed database with sample laundry data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding database...')
        
        # 1. Ensure a user exists (Owner)
        owner, created = User.objects.get_or_create(
            email='owner@example.com',
            defaults={
                'phone': '0240000001',
                'first_name': 'Sample',
                'last_name': 'Owner',
                'is_verified': True,
                'role': User.Role.OWNER
            }
        )
        if created:
            owner.set_password('password123')
            owner.save()

        # 2. Categories
        categories_names = ['Wash & Fold', 'Dry Cleaning', 'Ironing', 'Duvet Cleaning']
        categories = []
        for name in categories_names:
            cat, _ = Category.objects.get_or_create(name=name)
            categories.append(cat)

        # 3. Laundries
        laundries_data = [
            {
                'name': 'Sparkle Cleaners',
                'description': 'Premium laundry service in Accra.',
                'latitude': 5.6037,
                'longitude': -0.1870,
                'price_range': Laundry.PriceRange.MEDIUM,
                'is_featured': True
            },
            {
                'name': 'Express Wash',
                'description': 'Fastest wash and fold in town.',
                'latitude': 5.6148,
                'longitude': -0.2058,
                'price_range': Laundry.PriceRange.LOW,
                'is_featured': False
            },
            {
                'name': 'Elite Dry Clean',
                'description': 'Professional care for your fine garments.',
                'latitude': 5.5900,
                'longitude': -0.1750,
                'price_range': Laundry.PriceRange.HIGH,
                'is_featured': True
            }
        ]

        for ld in laundries_data:
            laundry, l_created = Laundry.objects.get_or_create(
                name=ld['name'],
                defaults={
                    'description': ld['description'],
                    'latitude': ld['latitude'],
                    'longitude': ld['longitude'],
                    'price_range': ld['price_range'],
                    'is_featured': ld['is_featured'],
                    'owner': owner,
                    'phone_number': '0245555555',
                    'address': f"Location for {ld['name']}"
                }
            )
            
            # 4. Services for each laundry
            if l_created:
                for cat in categories:
                    Service.objects.create(
                        laundry=laundry,
                        category=cat,
                        name=f"{cat.name} Special",
                        description=f"Our best {cat.name} service.",
                        base_price=random.randint(10, 50)
                    )

        self.stdout.write(self.style.SUCCESS('Successfully seeded database!'))

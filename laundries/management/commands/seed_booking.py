import random
from decimal import Decimal
# pyre-ignore[missing-module]
from django.core.management.base import BaseCommand
# pyre-ignore[missing-module]
from laundries.models import Laundry, Category, LaundryService
# pyre-ignore[missing-module]
from ordering.models import LaunderableItem

class Command(BaseCommand):
    help = 'Seed database with sample laundry custom pricing data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding dummy custom pricing...')
        
        laundries = Laundry.objects.all()
        if not laundries.exists():
            self.stdout.write(self.style.ERROR('No laundries found. Run seed_laundries first.'))
            return

        items = LaunderableItem.objects.filter(is_active=True)
        if not items.exists():
            self.stdout.write(self.style.ERROR('No global items found. Populate LaunderableItems first.'))
            return
            
        services = Category.objects.filter(type='SERVICE_TYPE')
        if not services.exists():
            self.stdout.write(self.style.ERROR('No service types found. Populate Categories first.'))
            return

        seeded_count = 0
        
        for laundry in laundries:
            # Randomly pick 5-10 items to offer
            offered_items = random.sample(list(items), min(len(items), random.randint(5, 10)))
            
            for item in offered_items:
                # Randomly pick 1-3 services for this item
                offered_services = random.sample(list(services), min(len(services), random.randint(1, 3)))
                
                for service in offered_services:
                    base_price = Decimal(str(random.randint(15, 60)))
                    
                    # Create or update price configuration
                    _, created = LaundryService.objects.get_or_create(
                        laundry=laundry,
                        item=item,
                        service_type=service,
                        defaults={
                            'price': base_price,
                            'estimated_duration': random.choice(['24 hours', '48 hours', 'Same Day']),
                            'is_available': True
                        }
                    )
                    if created:
                        seeded_count += 1
                        
        self.stdout.write(self.style.SUCCESS(f'Successfully created {seeded_count} price configurations!'))

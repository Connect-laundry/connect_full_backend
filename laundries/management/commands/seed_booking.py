import random
from decimal import Decimal
# pyre-ignore[missing-module]
from django.core.management.base import BaseCommand
# pyre-ignore[missing-module]
from laundries.models import Laundry, Category, LaundryService
# pyre-ignore[missing-module]
from ordering.models import LaunderableItem, BookingSlot
from django.utils import timezone
from datetime import timedelta

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
                    base_price = Decimal(str(random.randint(15, 60)))  # nosec B311
                    
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

        # Seed Booking Slots
        self.stdout.write('Seeding booking slots for the next 7 days...')
        slots_count = 0
        now = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        for laundry in laundries:
            for day in range(1, 8): # Next 7 days
                date = now + timedelta(days=day)
                # Create 3 slots per day: Morning, Afternoon, Evening
                time_windows = [
                    (8, 12),  # 8 AM - 12 PM
                    (13, 17), # 1 PM - 5 PM
                    (18, 21)  # 6 PM - 9 PM
                ]
                for start_h, end_h in time_windows:
                    start_time = date.replace(hour=start_h)
                    end_time = date.replace(hour=end_h)
                    
                    _, created = BookingSlot.objects.get_or_create(
                        laundry=laundry,
                        start_time=start_time,
                        end_time=end_time,
                        defaults={
                            'is_available': True,
                            'max_bookings': random.randint(3, 8),
                            'current_bookings': 0
                        }
                    )
                    if created:
                        slots_count += 1
                        
        self.stdout.write(self.style.SUCCESS(f'Successfully created {slots_count} booking slots!'))

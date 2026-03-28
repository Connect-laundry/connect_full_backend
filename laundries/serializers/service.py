from rest_framework import serializers
from laundries.models.service import LaundryService
from laundries.models.category import Category
from marketplace.models import LaunderableItem

class LaundryServiceSerializer(serializers.ModelSerializer):
    """
    Serializer for the LaundryService model.
    Used by administration for managing services.
    """
    item_name = serializers.ReadOnlyField(source='item.name')
    service_type_name = serializers.ReadOnlyField(source='service_type.name')

    class Meta:
        model = LaundryService
        fields = [
            'id', 'laundry', 'item', 'item_name', 
            'service_type', 'service_type_name', 
            'price', 'is_available'
        ]

    def validate(self, data):
        """
        Validate that the service_type (Category) and item combined are unique for a laundry.
        """
        laundry = data.get('laundry')
        item = data.get('item')
        service_type = data.get('service_type')

        if not self.instance:
            if LaundryService.objects.filter(laundry=laundry, item=item, service_type=service_type).exists():
                raise serializers.ValidationError("This service for this item already exists for this laundry.")
        
        return data

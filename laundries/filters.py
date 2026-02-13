# pyre-ignore[missing-module]
from django_filters import rest_framework as filters
from .models.laundry import Laundry

class LaundryFilter(filters.FilterSet):
    category = filters.UUIDFilter(field_name='services__category', distinct=True)
    featured = filters.BooleanFilter(field_name='is_featured')
    price_range = filters.CharFilter(field_name='price_range')

    class Meta:
        model = Laundry
        fields = ['featured', 'category', 'price_range']

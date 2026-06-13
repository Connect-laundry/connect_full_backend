from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from .models.laundry import Laundry
from .models.opening_hours import OpeningHours
from .models.review import Review
from .models.favorite import Favorite
from .models.pricing import LaundryPricingItem, LaundryWeightPricing
from .models.price_import import PriceListImportJob, PriceListDraftItem
from django.utils.html import format_html


class OpeningHoursInline(TabularInline):
    model = OpeningHours
    extra = 1


class LaundryPricingItemInline(TabularInline):
    model = LaundryPricingItem
    extra = 0
    fields = ('item_name', 'category', 'unit_price', 'is_active', 'display_order')


@admin.register(Laundry)
class LaundryAdmin(ModelAdmin):
    list_display = (
        'name',
        'owner',
        'price_range',
        'pricing_model',
        'display_featured',
        'display_active',
        'created_at'
    )
    list_filter = ('is_featured', 'is_active', 'price_range', 'pricing_model')
    search_fields = ('name', 'description', 'address')
    inlines = [OpeningHoursInline, LaundryPricingItemInline]
    readonly_fields = ('id', 'created_at', 'updated_at')
    list_filter_sheet = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('owner')

    @display(description="Featured", boolean=True)
    def display_featured(self, obj):
        return obj.is_featured

    @display(description="Active", label={
        "True": "success",
        "False": "danger",
    })
    def display_active(self, obj):
        return str(obj.is_active)


@admin.register(Review)
class ReviewAdmin(ModelAdmin):
    list_display = ('laundry', 'user', 'rating_stars', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('comment', 'laundry__name', 'user__email')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('laundry', 'user')

    @display(description="Rating")
    def rating_stars(self, obj):
        return format_html(
            '<span class="text-yellow-500">{}</span>',
            "★" * obj.rating + "☆" * (5 - obj.rating)
        )


@admin.register(Favorite)
class FavoriteAdmin(ModelAdmin):
    list_display = ('user', 'laundry', 'created_at')
    search_fields = ('user__email', 'laundry__name')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'laundry')


@admin.register(LaundryPricingItem)
class LaundryPricingItemAdmin(ModelAdmin):
    list_display = ('item_name', 'laundry', 'category', 'unit_price', 'is_active', 'display_order')
    list_filter = ('is_active', 'category')
    search_fields = ('item_name', 'laundry__name')
    readonly_fields = ('id', 'created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('laundry')


@admin.register(LaundryWeightPricing)
class LaundryWeightPricingAdmin(ModelAdmin):
    list_display = ('laundry', 'base_price_per_kg', 'minimum_charge', 'rounding_strategy', 'is_active')
    list_filter = ('is_active', 'rounding_strategy')
    search_fields = ('laundry__name',)
    readonly_fields = ('id', 'created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('laundry')


class PriceListDraftItemInline(TabularInline):
    model = PriceListDraftItem
    extra = 0
    fields = ('item_name', 'suggested_price', 'category', 'confidence', 'is_selected')
    readonly_fields = ('confidence',)


@admin.register(PriceListImportJob)
class PriceListImportJobAdmin(ModelAdmin):
    list_display = ('id', 'laundry', 'status', 'provider', 'created_at', 'confirmed_at')
    list_filter = ('status', 'provider')
    search_fields = ('laundry__name',)
    readonly_fields = ('id', 'created_at', 'updated_at', 'confirmed_at')
    inlines = [PriceListDraftItemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('laundry')

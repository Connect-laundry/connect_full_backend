from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from .models.laundry import Laundry
from .models.opening_hours import OpeningHours
from .models.review import Review
from .models.favorite import Favorite
from django.utils.html import format_html


class OpeningHoursInline(TabularInline):
    model = OpeningHours
    extra = 1


@admin.register(Laundry)
class LaundryAdmin(ModelAdmin):
    list_display = (
        'name', 
        'owner', 
        'price_range', 
        'display_featured', 
        'display_active', 
        'created_at'
    )
    list_filter = ('is_featured', 'is_active', 'price_range')
    search_fields = ('name', 'description', 'address')
    inlines = [OpeningHoursInline]
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

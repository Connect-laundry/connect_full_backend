from django.contrib import admin
from .models.laundry import Laundry
from .models.opening_hours import OpeningHours
from .models.review import Review
from .models.favorite import Favorite

class OpeningHoursInline(admin.TabularInline):
    model = OpeningHours
    extra = 1

@admin.register(Laundry)
class LaundryAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'price_range', 'is_featured', 'is_active', 'created_at')
    list_filter = ('is_featured', 'is_active', 'price_range')
    search_fields = ('name', 'description', 'address')
    inlines = [OpeningHoursInline]
    readonly_fields = ('id', 'created_at', 'updated_at')

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('laundry', 'user', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('comment', 'laundry__name', 'user__email')

@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ('user', 'laundry', 'created_at')
    search_fields = ('user__email', 'laundry__name')

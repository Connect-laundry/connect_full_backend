from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import FAQ, Feedback, FailedTask


@admin.register(FAQ)
class FAQAdmin(ModelAdmin):
    list_display = ('question', 'display_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('question', 'answer')

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active


@admin.register(Feedback)
class FeedbackAdmin(ModelAdmin):
    list_display = ('user', 'subject', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('subject', 'message', 'user__email')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


@admin.register(FailedTask)
class FailedTaskAdmin(ModelAdmin):
    list_display = ('task_name', 'task_id', 'failed_at', 'retry_count')
    list_filter = ('failed_at', 'task_name')
    search_fields = ('task_name', 'task_id', 'exception')
    readonly_fields = ('task_id', 'task_name', 'exception', 'retry_count', 'failed_at')

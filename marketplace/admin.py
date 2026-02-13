from django.contrib import admin
from .models import FAQ, Feedback, FailedTask

@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ('question', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('question', 'answer')

@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('subject', 'message', 'user__email')

@admin.register(FailedTask)
class FailedTaskAdmin(admin.ModelAdmin):
    list_display = ('task_name', 'task_id', 'failed_at', 'retry_count')
    list_filter = ('failed_at', 'task_name')
    search_fields = ('task_name', 'task_id', 'exception')
    readonly_fields = ('task_id', 'task_name', 'args', 'kwargs', 'exception', 'stack_trace', 'retry_count', 'failed_at')

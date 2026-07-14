from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .models.laundry import Laundry, OwnerAuditLog
from .models.opening_hours import OpeningHours, HolidayOverride
from .models.review import Review
from .models.favorite import Favorite
from .models.pricing import (
    LaundryPricingItem, LaundryWeightPricing, PricingCatalogVersion,
    ScheduledPriceChange, DeliveryZonePricing
)
from .models.price_import import PriceListImportJob, PriceListDraftItem
from .services.approval import InvalidTransition, LaundryApprovalService


class OpeningHoursInline(TabularInline):
    model = OpeningHours
    # extra=0 is load-bearing: with extra=1 Django rendered a phantom blank row
    # (defaulting to Monday) on every change page. Any accidental touch made it
    # validate, producing "This field is required" and "Opening Hours with this
    # Laundry and Day already exists" for rows the owner never created. The
    # admin must show exactly the days the owner submitted — nothing more.
    extra = 0
    max_num = 7
    ordering = ('day',)


class LaundryPricingItemInline(TabularInline):
    model = LaundryPricingItem
    extra = 0
    fields = ('item_name', 'category', 'unit_price', 'is_active', 'display_order')
    ordering = ('display_order', 'item_name')


_STATUS_LABELS = {
    Laundry.ApprovalStatus.PENDING: "warning",
    Laundry.ApprovalStatus.APPROVED: "success",
    Laundry.ApprovalStatus.REJECTED: "danger",
    Laundry.ApprovalStatus.CHANGES_REQUESTED: "info",
    Laundry.ApprovalStatus.SUSPENDED: "danger",
}


@admin.register(Laundry)
class LaundryAdmin(ModelAdmin):
    list_display = (
        'name',
        'owner',
        'display_status',
        'city',
        'pricing_model',
        'display_active',
        'submitted_at',
        'created_at',
    )
    list_filter = ('status', 'is_featured', 'is_active', 'price_range', 'pricing_model', 'city')
    search_fields = ('name', 'description', 'address', 'owner__email')
    inlines = [OpeningHoursInline, LaundryPricingItemInline]
    readonly_fields = (
        'id', 'created_at', 'updated_at', 'submitted_at', 'approved_at',
        'rejected_at', 'changes_requested_at', 'reviewed_by', 'status_reason',
        'logo_preview', 'owner_contact', 'hours_summary',
    )
    list_filter_sheet = True
    date_hierarchy = 'created_at'
    actions = ['bulk_approve']

    # One-click decision buttons on the change page (top action bar) ...
    actions_detail = [
        'approve_laundry',
        'reject_laundry',
        'request_changes_laundry',
        'suspend_laundry',
    ]
    # ... and quick actions on every changelist row (no page open needed).
    actions_row = ['row_approve', 'row_reject']

    fieldsets = (
        ("Review Summary", {
            "fields": (
                'logo_preview', 'owner_contact', 'hours_summary',
            ),
        }),
        ("Approval", {
            "fields": (
                'status', 'status_reason', 'reviewed_by', 'submitted_at',
                'approved_at', 'rejected_at', 'changes_requested_at',
                'is_active', 'is_featured',
            ),
        }),
        ("Business Information", {
            "fields": (
                'name', 'description', 'image', 'phone_number', 'owner',
                'is_eco_friendly', 'ironing_available', 'vacation_mode',
            ),
        }),
        ("Location", {
            "fields": (
                'address', 'city', 'latitude', 'longitude',
                'service_radius_km', 'service_area_polygon',
            ),
            "classes": ("collapse",),
        }),
        ("Pricing & Service", {
            "fields": (
                'pricing_model', 'price_range', 'estimated_delivery_hours',
                'delivery_fee', 'pickup_fee', 'min_order',
            ),
            "classes": ("collapse",),
        }),
        ("System", {
            "fields": ('id', 'created_at', 'updated_at'),
            "classes": ("collapse",),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('owner', 'reviewed_by')

    # ------------------------------------------------------------ list display

    @display(description="Status", ordering='status', label=_STATUS_LABELS)
    def display_status(self, obj):
        return obj.status

    @display(description="Live", boolean=True, ordering='is_active')
    def display_active(self, obj):
        return obj.is_active

    # ------------------------------------------------------- review summaries

    @display(description="Logo")
    def logo_preview(self, obj):
        if not obj or not obj.image:
            return "No logo uploaded"
        try:
            url = obj.image.url
        except Exception:
            return "Logo unavailable (storage error)"
        return format_html(
            '<img src="{}" alt="logo" style="max-height:120px;border-radius:12px;" />', url
        )

    @display(description="Owner contact")
    def owner_contact(self, obj):
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return "—"
        owner_url = reverse('admin:users_user_change', args=[owner.pk])
        return format_html(
            '<a href="{}" class="text-primary-600 underline">{}</a><br>{}<br>{}',
            owner_url,
            owner.get_full_name() or owner.email,
            owner.email,
            getattr(owner, 'phone_number', '') or obj.phone_number or '',
        )

    @display(description="Opening hours (as submitted)")
    def hours_summary(self, obj):
        if obj is None or obj.pk is None:
            return "—"
        rows = obj.opening_hours.order_by('day')
        if not rows.exists():
            return "No opening hours submitted"
        return format_html(
            '<table style="border-collapse:collapse">{}</table>',
            format_html_join(
                '',
                '<tr><td style="padding:2px 16px 2px 0;font-weight:600">{}</td>'
                '<td style="padding:2px 0">{}</td></tr>',
                (
                    (
                        oh.get_day_display(),
                        "Closed" if oh.is_closed else (
                            f"{oh.opening_time:%H:%M} – {oh.closing_time:%H:%M}"
                            + (" (+1 day)" if oh.is_overnight else "")
                        ),
                    )
                    for oh in rows
                ),
            ),
        )

    # --------------------------------------------------------- decision engine

    def _decide(self, request, object_id, verb):
        """Shared handler for all decision buttons.

        Approve applies instantly. Reject / request changes / suspend show a
        one-field reason form first (GET), then apply on POST. All transitions
        run atomically through LaundryApprovalService.
        """
        laundry = self.get_object(request, object_id)
        if laundry is None:
            messages.error(request, "Laundry not found.")
            return redirect(reverse('admin:laundries_laundry_changelist'))

        transitions = {
            'approve': (LaundryApprovalService.approve, "approved ✔", False),
            'reject': (LaundryApprovalService.reject, "rejected", True),
            'request_changes': (LaundryApprovalService.request_changes, "sent back for changes", True),
            'suspend': (LaundryApprovalService.suspend, "suspended", True),
        }
        func, verb_past, needs_reason = transitions[verb]

        if needs_reason and request.method != 'POST':
            context = {
                **self.admin_site.each_context(request),
                'title': f"{verb.replace('_', ' ').title()} — {laundry.name}",
                'laundry': laundry,
                'verb': verb,
                'opts': self.model._meta,
            }
            return render(request, 'admin/laundries/laundry/approval_reason.html', context)

        kwargs = {'actor': request.user, 'request': request}
        if needs_reason:
            kwargs['reason'] = (request.POST.get('reason') or '').strip()

        try:
            func(laundry, **kwargs)
        except InvalidTransition as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"'{laundry.name}' was {verb_past}. The owner has been notified.",
            )
        return redirect(request.GET.get('next')
                        or reverse('admin:laundries_laundry_changelist'))

    @action(description="✔ Approve", url_path="approve", permissions=["change"],
            attrs={"class": "laundry-action laundry-action--approve"})
    def approve_laundry(self, request, object_id):
        return self._decide(request, object_id, 'approve')

    @action(description="✖ Reject", url_path="reject", permissions=["change"],
            attrs={"class": "laundry-action laundry-action--reject"})
    def reject_laundry(self, request, object_id):
        return self._decide(request, object_id, 'reject')

    @action(description="✎ Request Changes", url_path="request-changes", permissions=["change"],
            attrs={"class": "laundry-action laundry-action--changes"})
    def request_changes_laundry(self, request, object_id):
        return self._decide(request, object_id, 'request_changes')

    @action(description="⏸ Suspend", url_path="suspend", permissions=["change"],
            attrs={"class": "laundry-action laundry-action--suspend"})
    def suspend_laundry(self, request, object_id):
        return self._decide(request, object_id, 'suspend')

    # Quick actions available directly from the pending list.
    @action(description="Approve", url_path="row-approve", permissions=["change"])
    def row_approve(self, request, object_id):
        return self._decide(request, object_id, 'approve')

    @action(description="Reject", url_path="row-reject", permissions=["change"])
    def row_reject(self, request, object_id):
        return self._decide(request, object_id, 'reject')

    # Bulk approval from the changelist checkbox action menu.
    @admin.action(description="Approve selected laundries")
    def bulk_approve(self, request, queryset):
        done, skipped = 0, 0
        for laundry in queryset:
            try:
                LaundryApprovalService.approve(laundry, actor=request.user, request=request)
                done += 1
            except InvalidTransition:
                skipped += 1
        if done:
            messages.success(request, f"Approved {done} laundr{'y' if done == 1 else 'ies'}.")
        if skipped:
            messages.warning(
                request,
                f"Skipped {skipped} (already approved or not in an approvable state).",
            )


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


@admin.register(PricingCatalogVersion)
class PricingCatalogVersionAdmin(ModelAdmin):
    list_display = ('laundry', 'version_number', 'created_at')
    search_fields = ('laundry__name',)
    readonly_fields = ('id', 'created_at')

@admin.register(ScheduledPriceChange)
class ScheduledPriceChangeAdmin(ModelAdmin):
    list_display = ('laundry', 'effective_at', 'is_applied', 'created_at')
    list_filter = ('is_applied', 'effective_at')
    search_fields = ('laundry__name',)
    readonly_fields = ('id', 'created_at')

@admin.register(HolidayOverride)
class HolidayOverrideAdmin(ModelAdmin):
    list_display = ('laundry', 'date', 'opening_time', 'closing_time', 'is_closed', 'note')
    list_filter = ('is_closed', 'date')
    search_fields = ('laundry__name', 'note')
    readonly_fields = ('id',)

@admin.register(DeliveryZonePricing)
class DeliveryZonePricingAdmin(ModelAdmin):
    list_display = ('laundry', 'min_distance_km', 'max_distance_km', 'delivery_fee', 'pickup_fee')
    search_fields = ('laundry__name',)
    readonly_fields = ('id',)

@admin.register(OwnerAuditLog)
class OwnerAuditLogAdmin(ModelAdmin):
    list_display = ('laundry', 'actor', 'action', 'timestamp')
    list_filter = ('action', 'timestamp')
    search_fields = ('laundry__name', 'actor__email', 'action')
    readonly_fields = ('id', 'timestamp')

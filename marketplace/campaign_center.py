"""Campaign Center — staff UI for composing and sending push campaigns.

Mirrors the Connect Insights pattern (``config/insights.py``): plain
``@staff_member_required`` views rendering server-side templates that extend
the Unfold admin shell. Everything here delegates to the existing
``CampaignService`` / ``run_campaign`` pipeline — this module is presentation
and validation only, so the API (``marketplace/views/campaigns.py``) and the
UI can never drift apart in delivery behaviour.
"""
import json

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from marketplace.models import Notification, NotificationCampaign
from marketplace.services.audit import record_audit
from marketplace.services.campaign_service import CampaignService
from marketplace.services.notification_service import NotificationService

User = get_user_model()

Segment = NotificationCampaign.Segment
Status = NotificationCampaign.Status

# Which extra input each segment needs. Drives both the form's conditional
# fields and the hint shown next to the segment picker.
SEGMENT_PARAM_HELP = {
    Segment.ALL: '',
    Segment.ACTIVE: 'Uses "Days" — users who logged in within that window.',
    Segment.INACTIVE: 'Uses "Days" — users with no login in that window.',
    Segment.PENDING_ORDERS: '',
    Segment.UNPAID: '',
    Segment.PROMO_OPT_IN: '',
    Segment.ABANDONED_BOOKING: 'Uses "Hours" — pending, unpaid orders older than this.',
    Segment.REFERRAL: '',
    Segment.NEVER_ORDERED: '',
    Segment.CITY: 'Uses "City" — matched against saved delivery addresses.',
    Segment.CUSTOM: 'Uses "User emails" — one per line.',
}


class CampaignForm(forms.ModelForm):
    """Campaign fields plus flattened segment parameters.

    ``segment_params`` is a JSONField on the model; exposing raw JSON to an
    admin is a footgun, so each supported key gets its own typed input and
    ``clean`` reassembles them.
    """

    days = forms.IntegerField(
        required=False, min_value=1, max_value=365,
        help_text='For Active / Inactive segments. Default 14.',
    )
    hours = forms.IntegerField(
        required=False, min_value=1, max_value=720,
        help_text='For Abandoned booking. Default 6.',
    )
    city = forms.CharField(
        required=False, max_length=100,
        help_text='For the City segment.',
    )
    user_emails = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 4}),
        help_text='For the Custom segment — one email per line.',
    )

    class Meta:
        model = NotificationCampaign
        fields = [
            'name', 'title', 'body', 'segment', 'notification_type',
            'category', 'priority', 'action_url', 'image_url', 'promo_code',
            'expires_at',
        ]
        widgets = {
            'body': forms.Textarea(attrs={'rows': 4}),
            'expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }
        help_texts = {
            'name': 'Internal label. Never shown to users.',
            'title': 'Push title — keep under ~40 characters so it is not truncated.',
            'body': 'Push body — keep under ~120 characters.',
            'action_url': 'Where a tap lands, e.g. /orders, /referral, /promotions.',
            'promo_code': 'Optional code passed to the app in the push payload.',
            'category': 'Governs which notification preference gates this push.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Re-populate the flattened inputs when editing an existing campaign.
        params = (self.instance.segment_params or {}) if self.instance else {}
        if params and not self.is_bound:
            self.fields['days'].initial = params.get('inactive_days') or params.get('active_days')
            self.fields['hours'].initial = params.get('abandoned_hours')
            self.fields['city'].initial = params.get('city', '')
            ids = params.get('user_ids') or []
            if ids:
                emails = User.objects.filter(pk__in=ids).values_list('email', flat=True)
                self.fields['user_emails'].initial = '\n'.join(emails)

    def clean(self):
        cleaned = super().clean()
        segment = cleaned.get('segment')
        params = {}

        if segment == Segment.ACTIVE:
            params['active_days'] = cleaned.get('days') or 14
        elif segment == Segment.INACTIVE:
            params['inactive_days'] = cleaned.get('days') or 14
        elif segment == Segment.ABANDONED_BOOKING:
            params['abandoned_hours'] = cleaned.get('hours') or 6
        elif segment == Segment.CITY:
            city = (cleaned.get('city') or '').strip()
            if not city:
                self.add_error('city', 'A city is required for the City segment.')
            params['city'] = city
        elif segment == Segment.CUSTOM:
            raw = (cleaned.get('user_emails') or '').strip()
            emails = [line.strip() for line in raw.splitlines() if line.strip()]
            if not emails:
                self.add_error('user_emails', 'At least one email is required for the Custom segment.')
            found = {
                email.lower(): pk
                for email, pk in User.objects.filter(email__in=emails).values_list('email', 'pk')
            }
            missing = [e for e in emails if e.lower() not in found]
            if missing:
                self.add_error(
                    'user_emails',
                    'No account for: ' + ', '.join(missing[:5]) + ('…' if len(missing) > 5 else ''),
                )
            params['user_ids'] = [str(pk) for pk in found.values()]

        cleaned['segment_params'] = params
        return cleaned

    def save(self, commit=True):
        campaign = super().save(commit=False)
        campaign.segment_params = self.cleaned_data.get('segment_params', {})
        if commit:
            campaign.save()
        return campaign


def _audience_count(segment, params):
    """Live size of a segment, or None when the segment cannot be resolved."""
    try:
        return CampaignService.resolve_recipients(segment, params or {}).count()
    except Exception:
        return None


def _campaign_row(campaign):
    return {
        'obj': campaign,
        'audience': _audience_count(campaign.segment, campaign.segment_params),
        'detail_url': reverse('campaign-center-detail', args=[campaign.id]),
        'edit_url': reverse('campaign-center-edit', args=[campaign.id]),
    }


def _base_context(request, title):
    return {
        **admin.site.each_context(request),
        'title': title,
        'segments': Segment.choices,
        'statuses': Status.choices,
    }


@staff_member_required
def campaign_list_view(request):
    """Campaign Center home: portfolio totals plus every campaign."""
    campaigns = NotificationCampaign.objects.select_related('created_by')

    status_filter = (request.GET.get('status') or '').strip().upper()
    if status_filter and status_filter in Status.values:
        campaigns = campaigns.filter(status=status_filter)

    query = (request.GET.get('q') or '').strip()
    if query:
        campaigns = campaigns.filter(name__icontains=query)

    totals = NotificationCampaign.objects.aggregate(
        recipients=Sum('recipients_count'),
        delivered=Sum('delivered_count'),
        opened=Sum('opened_count'),
        clicked=Sum('clicked_count'),
        converted=Sum('converted_count'),
        revenue=Sum('revenue_generated'),
    )

    def rate(numerator, denominator):
        return round((numerator / denominator) * 100, 1) if denominator else 0.0

    recipients = totals['recipients'] or 0
    delivered = totals['delivered'] or 0

    context = _base_context(request, 'Campaign Center')
    context.update({
        'rows': [_campaign_row(c) for c in campaigns[:200]],
        'status_filter': status_filter,
        'query': query,
        'stats': {
            'total': NotificationCampaign.objects.count(),
            'sent': NotificationCampaign.objects.filter(status=Status.SENT).count(),
            'scheduled': NotificationCampaign.objects.filter(status=Status.SCHEDULED).count(),
            'delivered': delivered,
            'open_rate': rate(totals['opened'] or 0, delivered),
            'click_rate': rate(totals['clicked'] or 0, delivered),
            'converted': totals['converted'] or 0,
            'revenue': totals['revenue'] or 0,
        },
        'new_url': reverse('campaign-center-new'),
    })
    return render(request, 'admin/campaigns/list.html', context)


@staff_member_required
def campaign_compose_view(request, pk=None):
    """Create or edit a campaign. Sent campaigns are read-only."""
    campaign = get_object_or_404(NotificationCampaign, pk=pk) if pk else None

    if campaign and campaign.status in (Status.SENDING, Status.SENT):
        messages.warning(request, 'A campaign that has already been sent cannot be edited.')
        return redirect('campaign-center-detail', pk=campaign.pk)

    if request.method == 'POST':
        form = CampaignForm(request.POST, instance=campaign)
        if form.is_valid():
            saved = form.save(commit=False)
            if campaign is None:
                saved.created_by = request.user
            saved.save()
            messages.success(request, f'Saved “{saved.name}”.')
            return redirect('campaign-center-detail', pk=saved.pk)
        messages.error(request, 'Please correct the errors below.')
    else:
        form = CampaignForm(instance=campaign)

    context = _base_context(request, 'Edit campaign' if campaign else 'New campaign')
    context.update({
        'form': form,
        'campaign': campaign,
        'segment_help_json': json.dumps(dict(SEGMENT_PARAM_HELP)),
        'audience_url': reverse('campaign-center-audience'),
        'list_url': reverse('campaign-center'),
    })
    return render(request, 'admin/campaigns/compose.html', context)


@staff_member_required
def campaign_detail_view(request, pk):
    """Per-campaign analytics plus the send / schedule / test controls."""
    campaign = get_object_or_404(NotificationCampaign, pk=pk)

    recent = (
        Notification.objects.filter(campaign=campaign)
        .select_related('user')
        .order_by('-created_at')[:25]
    )

    context = _base_context(request, campaign.name)
    context.update({
        'campaign': campaign,
        'audience': _audience_count(campaign.segment, campaign.segment_params),
        'recent': recent,
        'can_send': campaign.status in (Status.DRAFT, Status.SCHEDULED, Status.FAILED),
        'edit_url': reverse('campaign-center-edit', args=[campaign.id]),
        'list_url': reverse('campaign-center'),
        'send_url': reverse('campaign-center-send', args=[campaign.id]),
        'schedule_url': reverse('campaign-center-schedule', args=[campaign.id]),
        'test_url': reverse('campaign-center-test', args=[campaign.id]),
        'default_test_email': request.user.email,
    })
    return render(request, 'admin/campaigns/detail.html', context)


@staff_member_required
def campaign_audience_view(request):
    """JSON audience size, so the compose form can update as the admin types."""
    segment = (request.GET.get('segment') or '').strip()
    if segment not in Segment.values:
        return JsonResponse({'error': 'Unknown segment.'}, status=400)

    params = {}
    if segment == Segment.ACTIVE:
        params['active_days'] = int(request.GET.get('days') or 14)
    elif segment == Segment.INACTIVE:
        params['inactive_days'] = int(request.GET.get('days') or 14)
    elif segment == Segment.ABANDONED_BOOKING:
        params['abandoned_hours'] = int(request.GET.get('hours') or 6)
    elif segment == Segment.CITY:
        params['city'] = (request.GET.get('city') or '').strip()
    elif segment == Segment.CUSTOM:
        emails = [e.strip() for e in (request.GET.get('user_emails') or '').splitlines() if e.strip()]
        params['user_ids'] = list(
            User.objects.filter(email__in=emails).values_list('pk', flat=True)
        )

    count = _audience_count(segment, params)
    if count is None:
        return JsonResponse({'error': 'Segment could not be resolved.'}, status=400)
    return JsonResponse({'count': count})


@staff_member_required
@require_POST
def campaign_send_view(request, pk):
    """Send now. Delegates to the same path the API uses."""
    from marketplace.services.campaign_service import CampaignDispatchResult

    campaign = get_object_or_404(NotificationCampaign, pk=pk)
    result = CampaignService.dispatch(campaign)

    if result.outcome == CampaignDispatchResult.ALREADY_SENDING:
        messages.warning(request, 'That campaign is already sending.')
    elif result.outcome == CampaignDispatchResult.EMPTY:
        messages.error(request, 'This segment currently matches no users — nothing was sent.')
    elif result.outcome == CampaignDispatchResult.UNAVAILABLE:
        messages.error(
            request,
            'Delivery queue is unavailable. The campaign stays scheduled and will send '
            'once the queue recovers.',
        )
    else:
        record_audit(
            action='campaign.send', request=request,
            target_type='NotificationCampaign', target_id=str(campaign.id),
            target_repr=campaign.name,
            metadata={'via': 'campaign_center', 'audience': result.audience},
        )
        messages.success(
            request, f'“{campaign.name}” queued for {result.audience} recipient(s).'
        )

    return redirect('campaign-center-detail', pk=pk)


@staff_member_required
@require_POST
def campaign_schedule_view(request, pk):
    """Schedule for later. Requires the Celery beat worker to actually fire."""
    from django.utils.dateparse import parse_datetime

    campaign = get_object_or_404(NotificationCampaign, pk=pk)
    raw = (request.POST.get('scheduled_for') or '').strip()
    parsed = parse_datetime(raw) if raw else None
    if parsed is None:
        messages.error(request, 'Pick a valid date and time.')
        return redirect('campaign-center-detail', pk=pk)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    if parsed <= timezone.now():
        messages.error(request, 'Choose a time in the future.')
        return redirect('campaign-center-detail', pk=pk)

    campaign.scheduled_for = parsed
    campaign.status = Status.SCHEDULED
    campaign.save(update_fields=['scheduled_for', 'status'])

    record_audit(
        action='campaign.schedule', request=request,
        target_type='NotificationCampaign', target_id=str(campaign.id),
        target_repr=campaign.name, metadata={'scheduled_for': parsed.isoformat()},
    )
    messages.success(request, f'Scheduled for {timezone.localtime(parsed):%d %b %Y, %H:%M}.')
    return redirect('campaign-center-detail', pk=pk)


@staff_member_required
@require_POST
def campaign_test_view(request, pk):
    """Send this campaign to one account only, without touching its counters."""
    campaign = get_object_or_404(NotificationCampaign, pk=pk)
    email = (request.POST.get('email') or '').strip()
    if not email:
        messages.error(request, 'Enter an email to send the test to.')
        return redirect('campaign-center-detail', pk=pk)

    target = User.objects.filter(email__iexact=email).first()
    if target is None:
        messages.error(request, f'No account found for {email}.')
        return redirect('campaign-center-detail', pk=pk)

    # No campaign= and no dedup_key: this must never move campaign analytics
    # and must be re-sendable as many times as the admin needs.
    NotificationService.notify_user(
        target,
        title=campaign.title,
        body=campaign.body,
        type=campaign.notification_type,
        category=campaign.category,
        priority=campaign.priority,
        action_url=campaign.action_url,
        promo_code=campaign.promo_code,
        push=True,
    )

    device_count = target.push_devices.filter(is_active=True).count()
    if device_count:
        messages.success(request, f'Test sent to {email} ({device_count} device(s)).')
    else:
        messages.warning(
            request,
            f'{email} has no registered device, so only the in-app notification was created. '
            'Sign in on a dev-client or release build to register one.',
        )
    return redirect('campaign-center-detail', pk=pk)

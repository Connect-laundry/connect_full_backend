# pyre-ignore[missing-module]
from django.db.models import Q
# pyre-ignore[missing-module]
from django.http import HttpResponse, Http404
# pyre-ignore[missing-module]
from django.shortcuts import get_object_or_404
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.utils.html import escape
# pyre-ignore[missing-module]
from django.views import View
# pyre-ignore[missing-module]
from rest_framework import permissions, serializers, status
# pyre-ignore[missing-module]
from rest_framework.authentication import SessionAuthentication
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from config.throttling import LegalPublicThrottle
from marketplace.models import AuditLog
from marketplace.models.legal import LegalPage, UserLegalAcceptance
from marketplace.permissions import IsPlatformAdmin
from marketplace.serializers import (
    LegalAcceptanceSerializer,
    LegalPagePublicSerializer,
    LegalPageSerializer,
    LegalPageSummarySerializer,
)
from marketplace.services.audit import record_audit
from marketplace.services.legal import (
    archive_legal_page,
    create_new_legal_version,
    get_published_legal_page,
    latest_published_legal_pages,
    publish_legal_page,
    record_legal_acceptance,
    rollback_legal_page,
)
from users.auth.authentication import ClerkOrJWTAuthentication


class LegalListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = LegalPageSummarySerializer(many=True)


class LegalDetailResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = LegalPagePublicSerializer()


class LegalCurrentVersionItemSerializer(LegalPageSummarySerializer):
    accepted = serializers.BooleanField()
    accepted_version = serializers.CharField(allow_blank=True)
    needs_reacceptance = serializers.BooleanField()

    class Meta(LegalPageSummarySerializer.Meta):
        fields = LegalPageSummarySerializer.Meta.fields + [
            'accepted', 'accepted_version', 'needs_reacceptance',
        ]


class LegalCurrentVersionsResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = LegalCurrentVersionItemSerializer(many=True)


class LegalAcceptanceListDataSerializer(serializers.Serializer):
    accepted = LegalAcceptanceSerializer(many=True)
    pending_required = LegalCurrentVersionItemSerializer(many=True)


class LegalAcceptanceListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = LegalAcceptanceListDataSerializer()


class LegalAcceptanceCreateSerializer(serializers.Serializer):
    slug = serializers.CharField(required=False, allow_blank=True)
    legal_page = serializers.UUIDField(required=False)
    platform = serializers.CharField(required=False, allow_blank=True, max_length=40)
    app_version = serializers.CharField(required=False, allow_blank=True, max_length=40)

    def validate(self, attrs):
        if not attrs.get('slug') and not attrs.get('legal_page'):
            raise serializers.ValidationError('Provide slug or legal_page.')
        return attrs


class LegalAcceptanceCreateResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = LegalAcceptanceSerializer()


class LegalPublishArchiveSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class LegalAdminResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = LegalPageSerializer()


class LegalAdminUpdateResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = serializers.DictField()


def _success(message, data, http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'message': message, 'data': data}, status=http_status)


def _not_found(message='Legal document not found.'):
    return Response({'status': 'error', 'message': message, 'data': {}}, status=status.HTTP_404_NOT_FOUND)


class LegalDocumentListView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LegalPublicThrottle]

    @extend_schema(
        operation_id='legal_documents_list',
        parameters=[
            OpenApiParameter('q', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('document_type', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('language', OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses=LegalListResponseSerializer,
    )
    def get(self, request):
        language_code = request.query_params.get('language') or 'en'
        qs = latest_published_legal_pages(language_code=language_code)

        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(slug__icontains=q)
                | Q(content_markdown__icontains=q)
                | Q(summary__icontains=q)
            )

        document_type = request.query_params.get('document_type')
        if document_type:
            qs = qs.filter(document_type=LegalPage.normalize_document_type(document_type))

        return _success('Legal documents retrieved.', LegalPageSummarySerializer(qs, many=True).data)


class LegalDocumentDetailView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LegalPublicThrottle]

    @extend_schema(
        operation_id='legal_documents_retrieve',
        parameters=[
            OpenApiParameter('slug', OpenApiTypes.STR, OpenApiParameter.PATH),
            OpenApiParameter('version', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('language', OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses=LegalDetailResponseSerializer,
    )
    def get(self, request, slug=None, type=None):
        lookup = slug or type
        page = get_published_legal_page(
            lookup,
            version=request.query_params.get('version'),
            language_code=request.query_params.get('language') or 'en',
        )
        if page is None:
            return _not_found()
        return _success('Legal document retrieved.', LegalPagePublicSerializer(page).data)


class LegalCurrentVersionsView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=LegalCurrentVersionsResponseSerializer)
    def get(self, request):
        data = _current_versions_for_user(request.user)
        return _success('Current legal versions retrieved.', data)


class LegalUserAcceptanceView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=LegalAcceptanceListResponseSerializer)
    def get(self, request):
        accepted = UserLegalAcceptance.objects.select_related('legal_page').filter(user=request.user)
        data = {
            'accepted': LegalAcceptanceSerializer(accepted, many=True).data,
            'pending_required': [
                item for item in _current_versions_for_user(request.user)
                if item['needs_reacceptance']
            ],
        }
        return _success('Legal acceptance status retrieved.', data)

    @extend_schema(
        request=LegalAcceptanceCreateSerializer,
        responses=LegalAcceptanceCreateResponseSerializer,
    )
    def post(self, request):
        serializer = LegalAcceptanceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        legal_page_id = serializer.validated_data.get('legal_page')
        if legal_page_id:
            page = get_object_or_404(LegalPage.objects.published(), pk=legal_page_id)
        else:
            page = get_published_legal_page(serializer.validated_data.get('slug'))
            if page is None:
                return _not_found()

        acceptance = record_legal_acceptance(
            page,
            user=request.user,
            request=request,
            platform=serializer.validated_data.get('platform', ''),
            app_version=serializer.validated_data.get('app_version', ''),
        )
        return _success('Legal document accepted.', LegalAcceptanceSerializer(acceptance).data, status.HTTP_201_CREATED)


class LegalAdminCreateView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]

    @extend_schema(request=LegalPageSerializer, responses=LegalAdminResponseSerializer)
    def post(self, request):
        serializer = LegalPageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        page = serializer.save(last_modified_by=request.user)
        if serializer.validated_data.get('is_published') is True:
            page = publish_legal_page(page, request=request, actor=request.user)
        record_audit(
            action=AuditLog.Action.LEGAL_DOCUMENT_CREATED,
            request=request,
            target_type='LegalPage',
            target_id=page.id,
            target_repr=f'{page.slug} v{page.version_number}',
            metadata={'slug': page.slug, 'version': page.version_number},
        )
        return _success('Legal document created.', LegalPageSerializer(page).data, status.HTTP_201_CREATED)


class LegalAdminDetailView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]

    @extend_schema(request=LegalPageSerializer, responses=LegalAdminUpdateResponseSerializer)
    def patch(self, request, pk):
        page = get_object_or_404(LegalPage, pk=pk)
        serializer = LegalPageSerializer(page, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        if page.is_published:
            clone = create_new_legal_version(
                page,
                changes=serializer.validated_data,
                request=request,
                actor=request.user,
            )
            data = {'new_version_created': True, 'page': LegalPageSerializer(clone).data}
            return _success('Published legal documents are immutable; a new draft version was created.', data)

        updated = serializer.save(last_modified_by=request.user)
        record_audit(
            action=AuditLog.Action.LEGAL_DOCUMENT_UPDATED,
            request=request,
            target_type='LegalPage',
            target_id=updated.id,
            target_repr=f'{updated.slug} v{updated.version_number}',
            metadata={'slug': updated.slug, 'version': updated.version_number},
        )
        data = {'new_version_created': False, 'page': LegalPageSerializer(updated).data}
        return _success('Legal document updated.', data)


class LegalAdminPublishView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]

    @extend_schema(request=LegalPublishArchiveSerializer, responses=LegalAdminResponseSerializer)
    def post(self, request):
        serializer = LegalPublishArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        page = get_object_or_404(LegalPage, pk=serializer.validated_data['id'])
        page = publish_legal_page(page, request=request, actor=request.user)
        return _success('Legal document published.', LegalPageSerializer(page).data)


class LegalAdminArchiveView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]

    @extend_schema(request=LegalPublishArchiveSerializer, responses=LegalAdminResponseSerializer)
    def post(self, request):
        serializer = LegalPublishArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        page = get_object_or_404(LegalPage, pk=serializer.validated_data['id'])
        page = archive_legal_page(page, request=request, actor=request.user)
        return _success('Legal document archived.', LegalPageSerializer(page).data)


class LegalAdminRollbackView(APIView):
    authentication_classes = [ClerkOrJWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]

    @extend_schema(request=LegalPublishArchiveSerializer, responses=LegalAdminResponseSerializer)
    def post(self, request):
        serializer = LegalPublishArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = get_object_or_404(LegalPage, pk=serializer.validated_data['id'])
        page = rollback_legal_page(source, request=request, actor=request.user)
        return _success('Legal document rolled back.', LegalPageSerializer(page).data)


class PublicLegalHtmlView(View):
    def get(self, request, slug=None):
        page = get_published_legal_page(slug or '')
        if page is None:
            raise Http404('Legal document not found.')
        title = page.seo_title or page.title
        description = page.seo_description or page.short_description or page.summary
        html = f"""<!doctype html>
<html lang="{escape(page.language_code)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)}</title>
  <meta name="description" content="{escape(description)}">
  <meta property="og:title" content="{escape(title)}">
  <meta property="og:description" content="{escape(description)}">
  <link rel="canonical" href="{escape(request.build_absolute_uri(page.public_path))}">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #111827; background: #f9fafb; }}
    main {{ max-width: 860px; margin: 0 auto; padding: 48px 20px 72px; background: #fff; min-height: 100vh; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    p, li, td {{ line-height: 1.65; }}
    table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
    td {{ border: 1px solid #d1d5db; padding: 10px; vertical-align: top; }}
    .meta {{ color: #6b7280; font-size: 14px; margin-bottom: 32px; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(page.title)}</h1>
    <div class="meta">Version {escape(page.version_number)} · Effective {escape((page.effective_date or page.published_at or timezone.now()).date().isoformat())}</div>
    {page.content_html}
  </main>
</body>
</html>"""
        return HttpResponse(html)


def _current_versions_for_user(user):
    pages = latest_published_legal_pages()
    accepted_rows = UserLegalAcceptance.objects.filter(
        user=user,
        legal_page__slug__in=[page.slug for page in pages],
    ).select_related('legal_page')
    accepted_by_slug = {}
    for row in accepted_rows.order_by('legal_page__slug', '-accepted_at'):
        accepted_by_slug.setdefault(row.legal_page.slug, row.accepted_version)

    data = []
    for page in pages:
        accepted_version = accepted_by_slug.get(page.slug, '')
        accepted = accepted_version == page.version_number
        item = LegalPageSummarySerializer(page).data
        item['accepted'] = accepted
        item['accepted_version'] = accepted_version
        item['needs_reacceptance'] = bool(page.requires_user_reacceptance and not accepted)
        data.append(item)
    return data


class SupportLegalDocumentListView(LegalDocumentListView):
    """Alias of LegalDocumentListView mounted at /api/v1/support/legal/."""

    @extend_schema(
        operation_id='support_legal_documents_list',
        parameters=[
            OpenApiParameter('q', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('document_type', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('language', OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses=LegalListResponseSerializer,
    )
    def get(self, request):
        return super().get(request)


class SupportLegalDocumentDetailView(LegalDocumentDetailView):
    """Alias of LegalDocumentDetailView mounted at /api/v1/support/legal/<type>/."""

    @extend_schema(
        operation_id='support_legal_documents_retrieve',
        parameters=[
            OpenApiParameter('slug', OpenApiTypes.STR, OpenApiParameter.PATH),
            OpenApiParameter('version', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('language', OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses=LegalDetailResponseSerializer,
    )
    def get(self, request, slug=None, type=None):
        return super().get(request, slug=slug, type=type)

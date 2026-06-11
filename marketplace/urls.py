from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.faq import FAQView
from .views.feedback import FeedbackView
from .views.notifications import NotificationViewSet
from .views.special_offer import SpecialOfferViewSet
from .views.legal import LegalDocumentDetailView, LegalDocumentListView

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'home/special-offers', SpecialOfferViewSet, basename='special-offer')

urlpatterns = [
    path('faqs/', FAQView.as_view(), name='faq-list'),        # Canonical endpoint
    path('help/faq/', FAQView.as_view(), name='faq'),         # Legacy alias (kept for compatibility)
    path('help/feedback/', FeedbackView.as_view(), name='feedback'),
    path('legal/', LegalDocumentListView.as_view(), name='support_legal_list'),
    path('legal/<str:type>/', LegalDocumentDetailView.as_view(), name='support_legal_detail'),
    path('', include(router.urls)),
]

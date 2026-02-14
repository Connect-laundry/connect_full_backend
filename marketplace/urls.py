from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.faq import FAQView
from .views.feedback import FeedbackView
from .views.notifications import NotificationViewSet
from .views.special_offer import SpecialOfferViewSet
from .views.legal import LegalDocumentView

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'home/special-offers', SpecialOfferViewSet, basename='special-offer')

urlpatterns = [
    path('help/faq/', FAQView.as_view(), name='faq'),
    path('help/feedback/', FeedbackView.as_view(), name='feedback'),
    path('support/legal/', LegalDocumentView.as_view(), name='legal_list'),
    path('support/legal/<str:type>/', LegalDocumentView.as_view(), name='legal_detail'),
    path('', include(router.urls)),
]

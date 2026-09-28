from django.urls import path

from .views import arkesel_sms_status

urlpatterns = [
    path('arkesel/sms/status/<str:token>/', arkesel_sms_status, name='arkesel-sms-status'),
]

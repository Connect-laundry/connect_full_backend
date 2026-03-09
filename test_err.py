from django.test import RequestFactory
from marketplace.views.special_offer import SpecialOfferViewSet
from laundries.views.laundry import LaundryViewSet
import traceback

rf = RequestFactory()

print("--- Testing SpecialOfferViewSet ---")
try:
    request = rf.get('/api/v1/support/home/special-offers/')
    response = SpecialOfferViewSet.as_view({'get': 'list'})(request)
    response.render()
    print("Special Offers Success:", response.status_code)
except Exception as e:
    traceback.print_exc()

print("--- Testing LaundryViewSet Featured ---")
try:
    request = rf.get('/api/v1/laundries/laundries/?is_featured=true')
    view = LaundryViewSet.as_view({'get': 'list'})
    response = view(request)
    response.render()
    print("Laundry Featured Success:", response.status_code)
except Exception as e:
    traceback.print_exc()

print("--- Testing LaundryViewSet Nearby ---")
try:
    request = rf.get('/api/v1/laundries/laundries/?nearby=true&radius=10')
    view = LaundryViewSet.as_view({'get': 'list'})
    response = view(request)
    response.render()
    print("Laundry Nearby Success:", response.status_code)
except Exception as e:
    traceback.print_exc()

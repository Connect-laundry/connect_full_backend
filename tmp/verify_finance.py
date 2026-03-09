import os
import sys
project_root = os.getcwd()
sys.path.append(project_root)
import django
from decimal import Decimal

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from ordering.services.finance_service import FinanceService
from unittest.mock import MagicMock

def verify_finance_settings():
    print("\n--- Financial Settings Verification ---")
    
    # 1. Check Settings
    try:
        print(f"TAX_RATE: {settings.TAX_RATE}")
        print(f"DELIVERY_FEE_BASE: {settings.DELIVERY_FEE_BASE}")
        print(f"PLATFORM_FEE_RATE: {settings.PLATFORM_FEE_RATE}")
    except AttributeError as e:
        print(f"[FAIL] Missing setting: {str(e)}")
        return

    # 2. Test FinanceService logic
    # Mock an order
    mock_order = MagicMock()
    mock_order.items.aggregate.return_value = {'total': Decimal('100.00')}
    mock_order.laundry.delivery_fee = None # Trigger fallback
    mock_order.coupon = None
    
    # Calculate breakdown
    try:
        breakdown = FinanceService.calculate_price_breakdown(mock_order)
        print("\n--- Calculation Breakdown for 100 GHS order ---")
        for key, value in breakdown.items():
            print(f"{key}: {value}")
        
        # Expected total = 100 + 10 (fallback) + 7 (tax) + 5 (platform) = 122.00
        if breakdown['total'] == '122.00':
            print("\n[SUCCESS] Financial calculations are robust and matching expected defaults!")
        else:
            print(f"\n[WARNING] Total {breakdown['total']} differs from expected 122.00. Please verify defaults.")
            
    except Exception as e:
        print(f"\n[FAIL] Calculation error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_finance_settings()

#!/bin/bash
# Auto-generated cURL commands from QA Suite
# Generated: 2026-03-28T21:28:07.532756

# Health Check
curl -X GET "https://connect-full-backend.onrender.com/health/"

# Swagger UI
curl -X GET "https://connect-full-backend.onrender.com/api/schema/swagger-ui/"

# Register Owner
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/register/" -H 'Content-Type: application/json' -d '{"email": "qa_owner_2b8ccc@test.com", "phone": "+2332b8ccc001", "first_name": "QA", "last_name": "Owner", "role": "OWNER", "password": "QaTest!2026Secure", "password_confirm": "QaTest!2026Secure"}'

# Register Customer
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/register/" -H 'Content-Type: application/json' -d '{"email": "qa_customer_2b8ccc@test.com", "phone": "+2332b8ccc002", "first_name": "QA", "last_name": "Customer", "role": "CUSTOMER", "password": "QaTest!2026Secure", "password_confirm": "QaTest!2026Secure"}'

# Register Mismatched Passwords
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/register/" -H 'Content-Type: application/json' -d '{"email": "bad_2b8ccc@test.com", "phone": "+2332b8ccc999", "first_name": "Bad", "last_name": "User", "role": "CUSTOMER", "password": "QaTest!2026Secure", "password_confirm": "WrongPass123!"}'

# Register Invalid Role (RIDER)
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/register/" -H 'Content-Type: application/json' -d '{"email": "rider_2b8ccc@test.com", "phone": "+2332b8ccc998", "first_name": "Bad", "last_name": "Rider", "role": "RIDER", "password": "QaTest!2026Secure", "password_confirm": "QaTest!2026Secure"}'

# Register Duplicate Email
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/register/" -H 'Content-Type: application/json' -d '{"email": "qa_owner_2b8ccc@test.com", "phone": "+2332b8ccc997", "first_name": "Dup", "last_name": "User", "role": "CUSTOMER", "password": "QaTest!2026Secure", "password_confirm": "QaTest!2026Secure"}'

# Login Owner
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/login/" -H 'Content-Type: application/json' -d '{"email": "qa_owner_2b8ccc@test.com", "password": "QaTest!2026Secure"}'

# Login Wrong Password
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/login/" -H 'Content-Type: application/json' -d '{"email": "qa_owner_2b8ccc@test.com", "password": "WrongPassword123!"}'

# Access Protected Route (No Token)
curl -X GET "https://connect-full-backend.onrender.com/api/v1/auth/me/"

# Access Protected Route (Bad Token)
curl -X GET "https://connect-full-backend.onrender.com/api/v1/auth/me/" -H "Authorization: Bearer <TOKEN>"

# Access Protected Route (Valid Token)
curl -X GET "https://connect-full-backend.onrender.com/api/v1/auth/me/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Token Refresh
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/token/refresh/" -H 'Content-Type: application/json' -d '{"refresh": "<REFRESH_TOKEN>"}'

# Get Service Types (Catalog)
curl -X GET "https://connect-full-backend.onrender.com/api/v1/booking/services/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Get Catalog Items                              
curl -X GET "https://connect-full-backend.onrender.com/api/v1/booking/items/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Schedule Without laundry_id
curl -X GET "https://connect-full-backend.onrender.com/api/v1/booking/schedule/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Create Laundry (Initial)
curl -X POST "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"name": "QA Laundry 2b8ccc", "description": "Automated QA test laundry", "address": "123 Test Street, Accra", "city": "Accra", "latitude": "5.603700", "longitude": "-0.187000", "phone_number": "+2332b8ccc100", "price_range": "$$", "estimated_delivery_hours": 24, "delivery_fee": "5.00", "pickup_fee": "2.00", "min_order": "10.00", "pricing_methods": ["PER_KG"], "price_per_kg": "15.00", "min_weight": "2.00", "opening_hours": [{"day": 1, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}, {"day": 2, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}, {"day": 3, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}, {"day": 4, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}, {"day": 5, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}, {"day": 6, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}, {"day": 7, "opening_time": "08:00:00", "closing_time": "20:00:00", "is_closed": false}]}'

# Add Service to Catalog
curl -X POST "https://connect-full-backend.onrender.com/api/v1/laundries/laundries/6b900bc3-06c2-47ba-a513-39b78b5a290b/services/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"item_id": "1de4017c-62f2-45fb-82cb-63f9258a6647", "service_type_id": "d3c889e9-c875-4490-a3b5-029c3d9d3d44", "price": "10.00", "is_available": true}'

# Create Duplicate Laundry
curl -X POST "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"name": "Duplicate Laundry", "address": "456 Dupe St", "city": "Accra", "latitude": "5.600000", "longitude": "-0.180000", "phone_number": "+233999999", "pricing_methods": ["PER_ITEM"]}'

# Customer Cannot Create Laundry
curl -X POST "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"name": "Customer Laundry", "address": "789 Customer St", "city": "Accra", "latitude": "5.600000", "longitude": "-0.180000", "phone_number": "+233888888"}'

# List Owner Laundries
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Get Laundry Detail
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/6b900bc3-06c2-47ba-a513-39b78b5a290b/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Update Laundry Pricing
curl -X PATCH "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/6b900bc3-06c2-47ba-a513-39b78b5a290b/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"price_per_kg": "20.00", "min_weight": "3.00", "description": "Updated by QA suite"}'

# Customer Cannot Update Laundry
curl -X PATCH "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/6b900bc3-06c2-47ba-a513-39b78b5a290b/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"description": "Hacked by customer"}'

# Get Opening Hours
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/dashboard/my-laundry/6b900bc3-06c2-47ba-a513-39b78b5a290b/hours/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Public Laundry Listing
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/laundries/"

# Featured Laundries
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/featured/"

# Search Laundries
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/laundries/?search=laundry"

# Public Laundry Detail
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/laundries/6b900bc3-06c2-47ba-a513-39b78b5a290b/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Create PER_KG Order
curl -X POST "https://connect-full-backend.onrender.com/api/v1/booking/create/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"laundry": "6b900bc3-06c2-47ba-a513-39b78b5a290b", "pricing_method": "PER_KG", "estimated_weight": "5.00", "pickup_date": "2026-03-29T21:27:37Z", "pickup_address": "123 Customer St, Accra", "delivery_address": "123 Customer St, Accra", "special_instructions": "QA Test Order - PER_KG"}'

# PER_KG Missing Weight
curl -X POST "https://connect-full-backend.onrender.com/api/v1/booking/create/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"laundry": "6b900bc3-06c2-47ba-a513-39b78b5a290b", "pricing_method": "PER_KG", "pickup_date": "2026-03-29T21:27:37Z", "pickup_address": "Test St"}'

# PER_ITEM Missing Items
curl -X POST "https://connect-full-backend.onrender.com/api/v1/booking/create/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"laundry": "6b900bc3-06c2-47ba-a513-39b78b5a290b", "pricing_method": "PER_ITEM", "pickup_date": "2026-03-29T21:27:37Z", "pickup_address": "Test St"}'

# List Customer Orders
curl -X GET "https://connect-full-backend.onrender.com/api/v1/orders/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# List Active Orders
curl -X GET "https://connect-full-backend.onrender.com/api/v1/orders/active/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Initialize Payment (Invalid Order)
curl -X POST "https://connect-full-backend.onrender.com/api/v1/payments/initialize/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"order_id": "2cf8e69f-d3e2-4ac6-9ab3-2d48382e039b", "amount": "100.00"}'

# Verify Payment (Invalid Ref)
curl -X GET "https://connect-full-backend.onrender.com/api/v1/payments/verify/invalid_ref_123/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Invalid JSON Payload
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/login/" -H "Content-Type: application/json"

# Missing Content-Type Header
curl -X POST "https://connect-full-backend.onrender.com/api/v1/auth/login/"

# Non-existent Endpoint
curl -X GET "https://connect-full-backend.onrender.com/api/v1/this-does-not-exist/"

# SQL Injection Attempt
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/laundries/?search='; DROP TABLE laundries_laundry; --"

# Extremely Large Weight Value
curl -X POST "https://connect-full-backend.onrender.com/api/v1/booking/create/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -H 'Content-Type: application/json' -d '{"laundry": "6b900bc3-06c2-47ba-a513-39b78b5a290b", "pricing_method": "PER_KG", "estimated_weight": "99999.99", "pickup_date": "2026-03-29T21:27:51Z", "pickup_address": "Test"}'

# Response Format: Profile
curl -X GET "https://connect-full-backend.onrender.com/api/v1/auth/me/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Response Format: Laundry Listing
curl -X GET "https://connect-full-backend.onrender.com/api/v1/laundries/laundries/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"

# Response Format: Catalog Services
curl -X GET "https://connect-full-backend.onrender.com/api/v1/booking/services/" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json"


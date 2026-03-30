from django.contrib.auth import get_user_model

User = get_user_model()
if not User.objects.filter(email="testadmin100@example.com").exists():
    User.objects.create_superuser(
        "testadmin100@example.com", "01234567890", "testpassword123"
    )

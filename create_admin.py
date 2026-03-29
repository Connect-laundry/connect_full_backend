from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(email='testadmin99@example.com').exists():
    User.objects.create_superuser('testadmin99@example.com', '01234567898', 'testpassword123')

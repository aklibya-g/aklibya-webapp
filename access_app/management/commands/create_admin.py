from django.core.management.base import BaseCommand
from access_app.models import SystemUser


class Command(BaseCommand):
    help = 'Create default admin user'

    def handle(self, *args, **options):
        if not SystemUser.objects.filter(username='admin').exists():
            user = SystemUser(
                username='admin',
                is_admin=True,
                is_active=True,
            )
            user.set_password('admin123')
            user.save()
            self.stdout.write(self.style.SUCCESS('Admin user created successfully'))
        else:
            self.stdout.write('Admin user already exists')

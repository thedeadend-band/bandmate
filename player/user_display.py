from .models import UserProfile


def user_display_name(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    try:
        display_name = user.bandmate_profile.display_name.strip()
    except UserProfile.DoesNotExist:
        display_name = ''
    name = display_name or user.get_username()
    return f'{name} (admin)' if user.is_staff else name

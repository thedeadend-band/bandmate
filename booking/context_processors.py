from .permissions import booking_role


def booking_context(request):
    return {'can_view_booking': bool(booking_role(
        request.user, getattr(request, 'active_group', None)))}

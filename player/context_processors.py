from .models import GroupSong


def group_context(request):
    group = getattr(request, 'active_group', None)
    if not request.user.is_authenticated or not group:
        return {'active_group': group, 'ready_celebrations': []}
    celebrations = list(
        GroupSong.objects.filter(group=group, status='active')
        .exclude(celebration_dismissals__user=request.user)
        .order_by('-ready_date', 'song_name')[:5]
    )
    return {
        'active_group': group,
        'ready_celebrations': celebrations,
    }

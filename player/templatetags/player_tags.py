from django import template

from player.user_display import user_display_name


register = template.Library()


@register.filter
def display_name(user):
    return user_display_name(user)

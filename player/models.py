from django.conf import settings
from django.db import models


class Playlist(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='playlists',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class PlaylistEntry(models.Model):
    playlist = models.ForeignKey(
        Playlist,
        on_delete=models.CASCADE,
        related_name='entries',
    )
    song_name = models.CharField(max_length=500)
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ['position']
        unique_together = [('playlist', 'position')]

    def __str__(self):
        return f'{self.playlist.name} #{self.position}: {self.song_name}'


class SiteSettings(models.Model):
    google_calendar_url = models.URLField(max_length=1000, blank=True, default='')

    class Meta:
        verbose_name_plural = 'Site settings'

    def __str__(self):
        return 'Site Settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

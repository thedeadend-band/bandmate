from django.conf import settings
from django.db import models


class Setlist(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='setlists',
    )
    date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class SetlistEntry(models.Model):
    setlist = models.ForeignKey(
        Setlist,
        on_delete=models.CASCADE,
        related_name='entries',
    )
    song_name = models.CharField(max_length=500, blank=True, default='')
    position = models.PositiveIntegerField()
    is_break = models.BooleanField(default=False)

    class Meta:
        ordering = ['position']
        unique_together = [('setlist', 'position')]

    def __str__(self):
        if self.is_break:
            return f'{self.setlist.name} #{self.position}: (Break)'
        return f'{self.setlist.name} #{self.position}: {self.song_name}'


class SongWizard(models.Model):
    STEPS = [
        ('song_info', 'Song Info'),
        ('tempo_key', 'Metadata Lookup'),
        ('lyrics', 'Lyrics'),
        ('band_details', 'Band Details'),
        ('track_source', 'Track Source'),
        ('download_upload', 'Download / Upload'),
        ('beat_detect', 'Beat Detection'),
        ('quantize', 'Quantize + Click'),
        ('stem_separation', 'Stem Separation'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ]
    STEP_KEYS = [s[0] for s in STEPS]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='song_wizards',
    )
    current_step = models.CharField(max_length=20, choices=STEPS, default='song_info')
    title = models.CharField(max_length=255, blank=True, default='')
    artist = models.CharField(max_length=255, blank=True, default='')
    tempo = models.IntegerField(null=True, blank=True)
    key = models.CharField(max_length=20, blank=True, default='')
    time_signature = models.CharField(max_length=10, default='4/4')

    # 'youtube' or 'upload'
    track_source = models.CharField(max_length=20, default='youtube')

    # YouTube selection: {"video_id": "...", "title": "...", "duration": "..."}
    youtube_selection = models.JSONField(default=dict, blank=True)

    detected_beats = models.JSONField(default=list, blank=True)
    adjusted_beats = models.JSONField(default=list, blank=True)

    band_details = models.JSONField(default=dict, blank=True)

    selected_stems = models.JSONField(default=list, blank=True)

    lyrics_content = models.TextField(blank=True, default='')
    lyric_offset_secs = models.FloatField(default=0)

    # Background task tracking
    bg_task_status = models.CharField(max_length=20, default='idle')
    bg_task_progress = models.IntegerField(default=0)
    bg_task_message = models.TextField(blank=True, default='')

    staging_dir = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'Wizard: {self.artist} – {self.title}' if self.title else f'Wizard #{self.pk}'

    def get_step_index(self):
        try:
            return self.STEP_KEYS.index(self.current_step)
        except ValueError:
            return 0

    def get_navigable_steps(self):
        """Return steps the user has completed or is currently on."""
        current_idx = self.get_step_index()
        return [
            {'key': key, 'label': label, 'index': i, 'active': key == self.current_step,
             'accessible': i <= current_idx}
            for i, (key, label) in enumerate(self.STEPS)
            if key not in ('complete', 'failed')
        ]


class SiteSettings(models.Model):
    google_calendar_url = models.URLField(max_length=1000, blank=True, default='')
    spotify_client_id = models.CharField(max_length=255, blank=True, default='')
    spotify_client_secret = models.CharField(max_length=255, blank=True, default='')
    getsongbpm_api_key = models.CharField(max_length=255, blank=True, default='')
    getsongkey_api_key = models.CharField(max_length=255, blank=True, default='')
    song_api_key = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name_plural = 'Site settings'

    def __str__(self):
        return 'Site Settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class StemSeparationJob(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('paused', 'Paused'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ]
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='stem_jobs',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    progress = models.IntegerField(default=0)
    message = models.TextField(blank=True, default='')
    selected_stems = models.JSONField(default=list, blank=True)
    staging_dir = models.CharField(max_length=500)
    song_dir = models.CharField(max_length=500, blank=True, default='')
    tempo = models.IntegerField(null=True, blank=True)
    key = models.CharField(max_length=20, blank=True, default='')
    time_signature = models.CharField(max_length=10, default='4/4')
    lyrics_content = models.TextField(blank=True, default='')
    lyric_offset_secs = models.FloatField(default=0)
    band_details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notified = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.artist} - {self.title} ({self.get_status_display()})'

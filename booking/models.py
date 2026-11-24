import uuid
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


def default_target_years():
    year = timezone.now().year
    return f'{year},{year + 1}'


class BookingSettings(models.Model):
    group = models.OneToOneField('player.Group', on_delete=models.CASCADE,
                                 related_name='booking_settings')
    enabled = models.BooleanField(default=False)
    search_center = models.CharField(max_length=200, blank=True)
    radius_miles = models.PositiveIntegerField(
        default=100, validators=[MinValueValidator(1), MaxValueValidator(10000)])
    genres = models.CharField(max_length=300, blank=True)
    search_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)])
    search_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)])
    excluded_cities = models.CharField(max_length=1000, blank=True)
    target_years = models.CharField(max_length=100, default=default_target_years, blank=True)
    ai_enabled = models.BooleanField(default=False)
    ai_model = models.CharField(max_length=120, blank=True)
    monthly_budget_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                                             validators=[MinValueValidator(0)])
    daily_request_limit = models.PositiveIntegerField(default=0)
    max_output_tokens = models.PositiveIntegerField(default=1000,
        validators=[MinValueValidator(1), MaxValueValidator(100000)])
    updated_at = models.DateTimeField(auto_now=True)


class BookingMembershipRole(models.Model):
    class Role(models.TextChoices):
        MANAGER = 'booking_manager', 'Booking manager'
        EDITOR = 'booking_editor', 'Booking editor'
        VIEWER = 'booking_viewer', 'Booking viewer'

    membership = models.OneToOneField('player.GroupMembership', on_delete=models.CASCADE,
                                      related_name='booking_role')
    role = models.CharField(max_length=24, choices=Role.choices)

    class Meta:
        constraints = [models.CheckConstraint(
            check=models.Q(role__in=['booking_manager', 'booking_editor', 'booking_viewer']),
            name='booking_valid_role')]


class BookingOpenAICredential(models.Model):
    group = models.OneToOneField('player.Group', on_delete=models.CASCADE)
    key_ciphertext = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    project_label = models.CharField(max_length=120, blank=True)
    project_id = models.CharField(max_length=120, blank=True)
    generation = models.PositiveIntegerField(default=1)
    tested_at = models.DateTimeField(null=True, blank=True)
    test_status = models.CharField(max_length=24, default='untested')
    available_models = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)


class BookingGmailConnection(models.Model):
    group = models.OneToOneField('player.Group', on_delete=models.CASCADE)
    # NULL releases the mailbox when disconnected; uniqueness also covers races.
    mailbox = models.EmailField(unique=True, null=True, blank=True)
    refresh_ciphertext = models.TextField(blank=True)
    granted_scopes = models.JSONField(default=list)
    is_connected = models.BooleanField(default=False)
    generation = models.PositiveIntegerField(default=1)
    connected_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    connected_at = models.DateTimeField(null=True, blank=True)
    tested_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    history_id = models.CharField(max_length=100, blank=True)
    history_scope_hash = models.CharField(max_length=64, blank=True)


class GmailOAuthState(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    session_digest = models.CharField(max_length=64)
    verifier_ciphertext = models.TextField()
    connection_generation = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    request_send = models.BooleanField(default=False)
    expected_mailbox = models.EmailField(blank=True)


class BookingJob(models.Model):
    class Kind(models.TextChoices):
        OPENAI_TEST = 'openai_test', 'OpenAI connection test'
        GMAIL_TEST = 'gmail_test', 'Gmail connection test'
        GMAIL_IMPORT = 'gmail_import', 'Targeted Gmail import'
        WEB_DISCOVERY = 'web_discovery', 'Public source discovery'
        WEB_SEARCH = 'web_search', 'Public opportunity and artist search'
        GEOCODE = 'geocode', 'Review location matches'
        AI_EXTRACT = 'ai_extract', 'AI evidence extraction'
        AI_DRAFT = 'ai_draft', 'Review AI draft arrangement'
        GMAIL_SEND = 'gmail_send', 'Approved Gmail send'
        GMAIL_RECONCILE = 'gmail_reconcile', 'Check uncertain send'
        GMAIL_REPLIES = 'gmail_replies', 'Read approved conversation replies'

    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    idempotency_key = models.UUIDField()
    credential_generation = models.PositiveIntegerField()
    status = models.CharField(max_length=24, default='pending')
    error_code = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField()
    lease_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'idempotency_key'], name='booking_job_idempotency')]
        indexes = [models.Index(fields=['status', 'run_after'])]


class BookingBudgetReservation(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    job_key = models.UUIDField()
    model = models.CharField(max_length=120)
    credential_generation = models.PositiveIntegerField()
    key_fingerprint = models.CharField(max_length=64)
    authorized_usd = models.DecimalField(max_digits=12, decimal_places=6)
    charged_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    status = models.CharField(max_length=20, default='reserved')
    period = models.DateField()
    day = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['group', 'job_key'], name='booking_budget_job'),
            models.CheckConstraint(check=models.Q(authorized_usd__gte=0, charged_usd__gte=0), name='booking_budget_nonnegative'),
        ]


class BookingAIUsage(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    job = models.ForeignKey(BookingJob, null=True, blank=True, on_delete=models.PROTECT)
    attempt = models.PositiveIntegerField(default=1)
    reservation = models.OneToOneField(BookingBudgetReservation, null=True, blank=True, on_delete=models.PROTECT)
    task_type = models.CharField(max_length=40)
    model = models.CharField(max_length=120, blank=True)
    key_fingerprint = models.CharField(max_length=64)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    pricing_version = models.CharField(max_length=100, default='connection-test-no-inference')
    result = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['job', 'attempt'], name='booking_usage_attempt')]


class BandRecord(models.Model):
    """Group-owned records with stable identity across names and human merges."""
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    identity = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    revision = models.PositiveIntegerField(default=1)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        for field in self._meta.fields:
            if field.is_relation and field.name != 'group' and getattr(self, field.attname):
                related = getattr(self, field.name)
                if hasattr(related, 'group_id') and related.group_id != self.group_id:
                    raise ValidationError({field.name: 'Choose a record from this band.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class NamedRecord(BandRecord):
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=160, blank=True)
    website = models.URLField(max_length=1000, blank=True)
    notes = models.TextField(blank=True, max_length=20000)
    tags = models.CharField(max_length=300, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class BookingOrganization(NamedRecord):
    category = models.CharField(max_length=80, blank=True)


class BookingVenue(NamedRecord):
    organization = models.ForeignKey(BookingOrganization, null=True, blank=True, on_delete=models.PROTECT)
    address = models.CharField(max_length=400, blank=True)
    location_evidence = models.CharField(max_length=1000, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)])

    def clean(self):
        super().clean()
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError('Provide both latitude and longitude, or neither.')


class BookingContact(NamedRecord):
    organization = models.ForeignKey(BookingOrganization, null=True, blank=True, on_delete=models.PROTECT)
    venue = models.ForeignKey(BookingVenue, null=True, blank=True, on_delete=models.PROTECT)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'email'], condition=~models.Q(email=''), name='booking_contact_email')]

    def clean(self):
        self.email = self.email.strip().lower()
        super().clean()


class BookingEventSeries(NamedRecord):
    organization = models.ForeignKey(BookingOrganization, null=True, blank=True, on_delete=models.PROTECT)
    venue = models.ForeignKey(BookingVenue, null=True, blank=True, on_delete=models.PROTECT)
    season = models.CharField(max_length=120, blank=True)


class BookingOpportunity(NamedRecord):
    class Stage(models.TextChoices):
        DISCOVERED = 'discovered', 'Discovered'
        REVIEW = 'needs_review', 'Needs review'
        RESEARCHING = 'researching', 'Researching'
        QUALIFIED = 'qualified', 'Qualified'
        READY = 'ready_to_contact', 'Ready to contact'
        CONTACTED = 'contacted', 'Contacted externally'
        AWAITING = 'awaiting_reply', 'Awaiting reply'
        FOLLOW_UP = 'follow_up_due', 'Follow-up due'
        APPLIED = 'applied', 'Applied'
        CONSIDERATION = 'under_consideration', 'Under consideration'
        OFFERED = 'offered', 'Offered'
        CONFIRMED = 'confirmed', 'Confirmed'
        DECLINED = 'declined', 'Declined'
        NOT_SELECTED = 'not_selected', 'Not selected'
        CLOSED = 'closed', 'Closed'

    organization = models.ForeignKey(BookingOrganization, null=True, blank=True, on_delete=models.PROTECT)
    venue = models.ForeignKey(BookingVenue, null=True, blank=True, on_delete=models.PROTECT)
    contact = models.ForeignKey(BookingContact, null=True, blank=True, on_delete=models.PROTECT)
    series = models.ForeignKey(BookingEventSeries, null=True, blank=True, on_delete=models.PROTECT)
    event_year = models.PositiveIntegerField(validators=[MinValueValidator(1900), MaxValueValidator(2200)])
    category = models.CharField(max_length=80, blank=True)
    stage = models.CharField(max_length=30, choices=Stage.choices, default=Stage.REVIEW)
    priority = models.PositiveSmallIntegerField(default=2, choices=[(1, 'Low'), (2, 'Normal'), (3, 'High')])
    event_date = models.DateField(null=True, blank=True)
    application_open_date = models.DateField(null=True, blank=True)
    verified_deadline = models.DateField(null=True, blank=True)
    deadline_source = models.URLField(max_length=1000, blank=True)
    deadline_excerpt = models.CharField(max_length=2000, blank=True)
    predicted_window_start = models.DateField(null=True, blank=True)
    predicted_window_end = models.DateField(null=True, blank=True)
    prediction_reason = models.CharField(max_length=1000, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL)
    genres = models.CharField(max_length=300, blank=True)
    prior_booking = models.BooleanField(default=False)
    response_status = models.CharField(max_length=20, default='unknown', choices=[
        ('unknown', 'Unknown'), ('responded', 'Previously responded'), ('no_reply', 'No reply recorded'), ('declined', 'Previously declined')])
    history_evidence = models.CharField(max_length=2000, blank=True)
    expected_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    fee_evidence = models.CharField(max_length=1000, blank=True)

    def clean(self):
        super().clean()
        if (self.prior_booking or self.response_status != 'unknown') and not self.history_evidence.strip():
            raise ValidationError('Booking history and responsiveness need an evidence note.')
        if self.expected_fee is not None and not self.fee_evidence.strip():
            raise ValidationError('An expected fee needs a source or explanatory note.')
        if self.event_date and self.event_date.year != self.event_year:
            raise ValidationError({'event_date': 'The event date must belong to the selected event year.'})
        if (self.verified_deadline or self.application_open_date) and (not self.deadline_source or not self.deadline_excerpt.strip()):
            raise ValidationError('Published application dates need their source and an excerpt identifying this event cycle.')
        if self.application_open_date and self.verified_deadline and self.application_open_date > self.verified_deadline:
            raise ValidationError('Applications cannot open after the deadline.')
        if bool(self.predicted_window_start) != bool(self.predicted_window_end):
            raise ValidationError('Provide both predicted window dates.')
        if self.predicted_window_start and (self.predicted_window_end < self.predicted_window_start or not self.prediction_reason.strip()):
            raise ValidationError('A predicted window needs ordered dates and an explanation.')
        if self.assigned_to_id and not self.group.memberships.filter(user_id=self.assigned_to_id, user__is_active=True).exists():
            raise ValidationError({'assigned_to': 'Choose an active member of this band.'})

    class Meta:
        indexes = [models.Index(fields=['group', 'event_year', 'stage'])]
        constraints = [models.UniqueConstraint(fields=['group', 'series', 'event_year'],
            condition=models.Q(series__isnull=False, is_archived=False), name='booking_series_year')]


class BookingIdentityAlias(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    identity = models.UUIDField()
    kind = models.CharField(max_length=20)
    value = models.CharField(max_length=1000)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'identity', 'kind', 'value'], name='booking_identity_alias')]
        indexes = [models.Index(fields=['group', 'kind', 'value'])]


class BookingSuppression(models.Model):
    class Kind(models.TextChoices):
        IDENTITY = 'identity', 'Record'
        EMAIL = 'email', 'Email address'
        DOMAIN = 'domain', 'Email or website domain'
        URL = 'url', 'Exact source URL'
        NAME_CITY = 'name_city', 'Name and city'

    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    value = models.CharField(max_length=1000)
    reason = models.CharField(max_length=1000)
    active = models.BooleanField(default=True)
    do_not_contact = models.BooleanField(default=False)
    created_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'kind', 'value'], name='booking_suppression_key')]


class BookingEvidence(BandRecord):
    record_identity = models.UUIDField()
    field_name = models.CharField(max_length=80)
    source_type = models.CharField(max_length=20)
    source_ref = models.CharField(max_length=1000)
    excerpt = models.TextField(max_length=2000, blank=True)
    content_hash = models.CharField(max_length=64)
    confidence = models.CharField(max_length=20, default='human_reviewed')
    event_year = models.PositiveIntegerField(null=True, blank=True)


class BookingFollowUp(BandRecord):
    opportunity = models.ForeignKey(BookingOpportunity, on_delete=models.CASCADE)
    due_date = models.DateField()
    note = models.CharField(max_length=1000, blank=True)
    completed = models.BooleanField(default=False)


class BookingResearchRun(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=24, choices=BookingJob.Kind.choices)
    request_key = models.UUIDField(default=uuid.uuid4, unique=True)
    job = models.OneToOneField(BookingJob, null=True, blank=True, on_delete=models.PROTECT)
    scope = models.JSONField(default=dict)
    scope_hash = models.CharField(max_length=64)
    credential_generation = models.PositiveIntegerField(default=0)
    mailbox_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=24, default='preview')
    checkpoint = models.JSONField(default=dict)
    processed = models.PositiveIntegerField(default=0)
    failures = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)


class BookingConversationReference(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    source_key = models.CharField(max_length=64)
    message_id = models.CharField(max_length=100, blank=True)
    thread_id = models.CharField(max_length=100, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    excerpt = models.CharField(max_length=1000, blank=True)
    correspondents = models.JSONField(default=list)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    purged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'source_key'], name='booking_message_key')]


class BookingCandidate(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    run = models.ForeignKey(BookingResearchRun, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=64)
    source_type = models.CharField(max_length=20)
    source_ref = models.CharField(max_length=1000, blank=True)
    title = models.CharField(max_length=200)
    proposed = models.JSONField(default=dict)
    analysis = models.JSONField(default=dict)
    excerpt = models.TextField(max_length=2000, blank=True)
    content_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default='pending')
    opportunity = models.ForeignKey(BookingOpportunity, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'source_key'], name='booking_candidate_key')]


class BookingResearchSchedule(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    source_run = models.OneToOneField(BookingResearchRun, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    next_due = models.DateTimeField()
    interval_days = models.PositiveIntegerField(default=7)


class BookingProviderGate(models.Model):
    provider = models.CharField(max_length=20, unique=True)
    last_started_at = models.DateTimeField(null=True)


class BookingProviderRequest(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    job = models.ForeignKey(BookingJob, on_delete=models.PROTECT)
    provider = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=64, default='started')

    class Meta:
        indexes = [models.Index(fields=['provider', 'created_at']), models.Index(fields=['group', 'provider', 'created_at'])]


class BookingArtistReference(NamedRecord):
    genres = models.CharField(max_length=300, blank=True)


class BookingArtistGig(NamedRecord):
    artist = models.ForeignKey(BookingArtistReference, on_delete=models.PROTECT)
    venue = models.ForeignKey(BookingVenue, null=True, blank=True, on_delete=models.PROTECT)
    event_date = models.DateField()
    source_excerpt = models.CharField(max_length=2000)

    def clean(self):
        super().clean()
        if not self.website or not self.source_excerpt.strip():
            raise ValidationError('A public artist gig needs its source URL and excerpt.')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'artist', 'name', 'event_date'], name='booking_artist_gig_key')]


class BookingEmailTemplate(BandRecord):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=30, choices=[('venue', 'Venue'), ('festival', 'Festival'),
        ('civic', 'Civic concert'), ('follow_up', 'Follow-up')], default='venue')

    def __str__(self):
        return self.name


class BookingTemplateVersion(BandRecord):
    template = models.ForeignKey(BookingEmailTemplate, on_delete=models.PROTECT, related_name='versions')
    number = models.PositiveIntegerField()
    subject = models.CharField(max_length=300)
    body = models.TextField(max_length=12000)
    created_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['template', 'number'], name='booking_template_version')]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Template versions are immutable. Save a new version.')
        super().save(*args, **kwargs)


class BookingDraft(BandRecord):
    opportunity = models.ForeignKey(BookingOpportunity, on_delete=models.PROTECT)
    contact = models.ForeignKey(BookingContact, on_delete=models.PROTECT)
    template_version = models.ForeignKey(BookingTemplateVersion, null=True, blank=True, on_delete=models.PROTECT)
    created_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    to = models.EmailField()
    cc = models.JSONField(default=list, blank=True)
    bcc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=300)
    body = models.TextField(max_length=12000)
    # Human-reviewed substitution values/provenance, not an AI approval.
    context = models.JSONField(default=dict, blank=True)
    ai_suggestion = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=24, default='draft')
    reply_to = models.ForeignKey('BookingOutreachMessage', null=True, blank=True, on_delete=models.PROTECT,
        related_name='followup_drafts')


class BookingOutreachMessage(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.PROTECT)
    draft = models.OneToOneField(BookingDraft, on_delete=models.PROTECT, related_name='outreach')
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    job = models.OneToOneField(BookingJob, on_delete=models.PROTECT)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True)
    snapshot = models.JSONField()
    content_hash = models.CharField(max_length=64)
    raw = models.TextField()
    rfc_message_id = models.CharField(max_length=200, unique=True)
    mailbox = models.EmailField()
    credential_generation = models.PositiveIntegerField()
    status = models.CharField(max_length=24, default='approved')
    gmail_message_id = models.CharField(max_length=100, blank=True)
    gmail_thread_id = models.CharField(max_length=100, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            immutable = ['group_id', 'draft_id', 'actor_id', 'job_id', 'idempotency_key', 'snapshot',
                'content_hash', 'raw', 'rfc_message_id', 'mailbox', 'credential_generation', 'expires_at']
            if any(getattr(old, field) != getattr(self, field) for field in immutable):
                raise ValidationError('Approved email content and identity are immutable.')
        super().save(*args, **kwargs)


class BookingMailRead(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    message = models.ForeignKey(BookingOutreachMessage, on_delete=models.PROTECT)
    job = models.OneToOneField(BookingJob, on_delete=models.PROTECT)
    retention_days = models.PositiveIntegerField(default=30)
    expires_at = models.DateTimeField()


class BookingReply(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    message = models.ForeignKey(BookingOutreachMessage, on_delete=models.PROTECT)
    reference = models.OneToOneField(BookingConversationReference, on_delete=models.PROTECT)


class BookingCalendarPublication(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    opportunity = models.ForeignKey(BookingOpportunity, on_delete=models.PROTECT)
    target = models.CharField(max_length=60)
    local_calendar = models.ForeignKey('calendar_integrations.BandmateLocalCalendar', null=True, on_delete=models.PROTECT)
    google_calendar = models.ForeignKey('calendar_integrations.ConnectedCalendar', null=True, on_delete=models.PROTECT)
    operation = models.ForeignKey('calendar_integrations.CalendarWriteOperation', on_delete=models.PROTECT)
    event_id = models.CharField(max_length=1024)
    linked = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'opportunity', 'target'], name='booking_calendar_publication')]

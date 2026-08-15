"""The whole domain, written up front.

Every model the pipeline will need is here in phase 2, even though phases 2
only serves two of them over HTTP. The point is that 0001_initial is one
honest migration describing the real shape of the problem, rather than a
scaffold followed by a trail of migrations that read as design-in-progress.

The chain is: a Scan holds one uploaded shelf photo. The detector cuts it into
Detections, one per spine. Each Detection is read, then matched against
CatalogBook. Whatever survives review becomes a LibraryBook.
"""

from django.db import models

from .constants import normalize_author, normalize_title


class CatalogBook(models.Model):
    """A canonical book. The thing a spine gets resolved *to*.

    This table is reference data loaded from catalog/catalog.csv, not user
    content -- nothing in the scan pipeline writes to it.
    """

    title = models.CharField(max_length=500)
    author = models.CharField(max_length=300)
    year = models.IntegerField(null=True, blank=True)

    # Editions retitled between markets, and titles a reader is likely to say
    # out loud that the spine does not print. "Northern Lights" is shelved as
    # "The Golden Compass" in the US; both have to find this row.
    alt_titles = models.JSONField(default=list, blank=True)

    # One physical spine, several works behind it. An omnibus has to win over
    # its own contents when the spine says the collection's name, and lose to
    # them when the spine says a single volume's name.
    is_omnibus = models.BooleanField(default=False)
    contained_titles = models.JSONField(default=list, blank=True)

    # Denormalized match keys. Recomputed on every save so they cannot drift
    # from the display strings above, and indexed because matching a shelf
    # means hundreds of lookups against them per photo.
    norm_title = models.CharField(max_length=500, db_index=True, editable=False)
    norm_author = models.CharField(max_length=300, db_index=True, editable=False)

    class Meta:
        ordering = ('title', 'author')
        indexes = [
            models.Index(fields=['norm_title', 'norm_author']),
        ]
        constraints = [
            # Title alone is not a key: two different books can share one. The
            # pair is what has to be unique. See AMBIGUITIES.md, case 4.
            models.UniqueConstraint(
                fields=['norm_title', 'norm_author'],
                name='unique_catalog_title_author',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} — {self.author}'

    def save(self, *args, **kwargs):
        self.norm_title = normalize_title(self.title)
        self.norm_author = normalize_author(self.author)
        super().save(*args, **kwargs)

    @property
    def all_titles(self) -> list[str]:
        """Every string that should resolve to this row."""
        return [self.title, *self.alt_titles]


class Scan(models.Model):
    """One uploaded shelf photo and the state of its pipeline run."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        DETECTING = 'detecting', 'Detecting spines'
        READING = 'reading', 'Reading spines'
        MATCHING = 'matching', 'Matching to catalog'
        COMPLETE = 'complete', 'Complete'
        FAILED = 'failed', 'Failed'

    # FileField rather than ImageField: ImageField validation needs Pillow,
    # which is not a dependency until the detector lands in phase 3.
    image = models.FileField(upload_to='scans/%Y/%m/')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    # User-facing sentence. Safe to render directly -- provider status codes
    # and raw payloads go to the log, not here.
    error = models.TextField(blank=True)

    # Machine-readable reason, so the client can branch without parsing prose.
    # The one that matters is whether retrying will help: a rate limit clears
    # on its own, a missing API key never does.
    error_code = models.CharField(max_length=40, blank=True, db_index=True)

    # Milliseconds per pipeline stage, e.g. {"detect": 812, "read": 3140}.
    # Stored rather than logged because the interesting question -- which stage
    # dominates, and how that changes with shelf size -- can only be answered
    # across many scans, and the read stage is the one that costs money.
    timings = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self) -> str:
        return f'Scan {self.pk} ({self.status})'


class Detection(models.Model):
    """One spine found in one photo, and everything learned about it.

    A Detection accumulates: the detector writes bbox/crop/confidence, the VLM
    writes the raw read, the matcher writes candidates/match/margin. It is the
    unit the review screen shows the user.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        AUTO_MATCHED = 'auto_matched', 'Auto-matched'
        NEEDS_REVIEW = 'needs_review', 'Needs review'
        CONFIRMED = 'confirmed', 'Confirmed'
        DISCARDED = 'discarded', 'Discarded'

    scan = models.ForeignKey(Scan, related_name='detections', on_delete=models.CASCADE)

    # Pixel box in the source image, [x1, y1, x2, y2], origin top-left.
    bbox = models.JSONField(default=list)
    crop = models.FileField(upload_to='crops/%Y/%m/', blank=True)

    # The detector's own confidence that this box is a book spine at all.
    # Distinct from any confidence in what the spine *says*.
    confidence = models.FloatField(default=0.0)

    # What the VLM read off the crop, before any catalog is consulted.
    raw_title = models.CharField(max_length=500, blank=True)
    raw_author = models.CharField(max_length=300, blank=True)

    # Ranked catalog candidates as scored dicts, best first. Kept even after a
    # match is chosen: the review screen offers the runners-up as the
    # correction options, so recomputing them per request would be wasteful.
    candidates = models.JSONField(default=list, blank=True)

    match = models.ForeignKey(
        CatalogBook,
        null=True,
        blank=True,
        related_name='detections',
        on_delete=models.SET_NULL,
    )

    # Score gap between the best candidate and the runner-up. This, not the
    # top score, is what decides auto-accept: a spine scoring 0.9 against two
    # near-identical entries is a coin flip, and belongs in review.
    margin = models.FloatField(default=0.0)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Set when this crop shows the same physical book as an earlier one in the
    # same scan. Shelved books touch, so the detector boxes some spines twice;
    # the reader is what notices, since only it can see that two crops carry
    # the same title. Kept rather than deleted so the review screen can show
    # "also found here" instead of silently dropping a real detection.
    duplicate_of = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        related_name='duplicates',
        on_delete=models.SET_NULL,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('scan', 'pk')

    def __str__(self) -> str:
        return self.raw_title or f'Detection {self.pk}'

    @property
    def top_score(self) -> float:
        return self.candidates[0].get('score', 0.0) if self.candidates else 0.0


class LibraryBook(models.Model):
    """A book the user has actually confirmed they own.

    Single-user for now -- there is no auth in this phase, so "the library" is
    the whole table. When accounts land this grows an owner FK and the
    uniqueness constraint below extends to include it.
    """

    class Source(models.TextChoices):
        SCAN = 'scan', 'From a scan'
        MANUAL = 'manual', 'Added by hand'

    # Null when the user kept a book the catalog does not have. The title and
    # author below are the record in that case, which is why they are stored
    # rather than read through the FK.
    catalog_book = models.ForeignKey(
        CatalogBook,
        null=True,
        blank=True,
        related_name='library_entries',
        on_delete=models.SET_NULL,
    )

    title = models.CharField(max_length=500)
    author = models.CharField(max_length=300, blank=True)

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.SCAN)

    # Kept for provenance: which spine in which photo produced this row.
    # SET_NULL so clearing old scans and their crops never deletes a library.
    detection = models.OneToOneField(
        Detection,
        null=True,
        blank=True,
        related_name='library_book',
        on_delete=models.SET_NULL,
    )

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-added_at',)
        constraints = [
            # Scanning the same shelf twice should not double the library.
            # Only applies to catalog-backed rows; unmatched keeps can repeat.
            models.UniqueConstraint(
                fields=['catalog_book'],
                condition=models.Q(catalog_book__isnull=False),
                name='unique_library_catalog_book',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} — {self.author}' if self.author else self.title

from django.db import models


class CatalogBook(models.Model):
    title = models.CharField(max_length=500)
    author = models.CharField(max_length=300)
    alt_titles = models.JSONField(default=list, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    series = models.CharField(max_length=300, blank=True)
    is_omnibus = models.BooleanField(default=False)
    contained_titles = models.JSONField(default=list, blank=True)

    # Spine appearance. Colour is what a person actually uses to pick between
    # two candidates on a review card ("mine's the purple one"), which is
    # exactly the moment the title and author have already failed to decide it.
    # Both are optional: blank means unknown, never "white".
    #
    # A colour *name*, not just a hex, because names survive the thing that
    # breaks hex -- exposure. The same purple spine sampled off a bright photo
    # and a dim one lands hundreds of RGB points apart.
    spine_color = models.CharField(max_length=40, blank=True)
    # Representative hex for rendering a swatch. The published cover colour,
    # not a measurement from any one photo.
    spine_hex = models.CharField(max_length=7, blank=True)

    def __str__(self):
        return f"{self.title} — {self.author}"


class Scan(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    image = models.ImageField(upload_to="scans/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    # Per-stage timings in ms: convert_ms, detect_ms, vlm_ms, match_ms, total_ms.
    timings = models.JSONField(default=dict, blank=True)
    used_full_image_fallback = models.BooleanField(default=False)
    empty_result = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Scan #{self.pk} ({self.status})"


class Detection(models.Model):
    STATUS_AUTO_ADDED = "auto_added"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_UNMATCHED = "unmatched"
    STATUS_UNREADABLE = "unreadable"
    STATUS_FAILED = "failed"
    STATUS_CONFIRMED = "confirmed"
    STATUS_DISCARDED = "discarded"
    STATUS_CHOICES = [
        (STATUS_AUTO_ADDED, "Auto-added"),
        (STATUS_NEEDS_REVIEW, "Needs review"),
        (STATUS_UNMATCHED, "Unmatched"),
        (STATUS_UNREADABLE, "Unreadable"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_DISCARDED, "Discarded"),
    ]

    scan = models.ForeignKey(Scan, related_name="detections", on_delete=models.CASCADE)
    bbox = models.JSONField(default=dict, blank=True)
    crop = models.ImageField(upload_to="crops/", blank=True, null=True)
    read_title = models.CharField(max_length=500, blank=True, null=True)
    read_author = models.CharField(max_length=300, blank=True, null=True)
    # Dominant spine colour as read off the crop, folded into
    # constants.SPINE_COLOR_VOCABULARY. Survives an unreadable title, so it is
    # sometimes the only evidence a detection carries.
    read_spine_color = models.CharField(max_length=40, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UNMATCHED)
    confidence = models.FloatField(null=True, blank=True)
    margin = models.FloatField(null=True, blank=True)
    # Top N candidates at match time: [{"book_id", "title", "author", "score"}, ...]
    candidates = models.JSONField(default=list, blank=True)
    chosen_book = models.ForeignKey(
        CatalogBook, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Detection #{self.pk} ({self.status})"


class LibraryEntry(models.Model):
    book = models.ForeignKey(CatalogBook, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    custom_title = models.CharField(max_length=500, blank=True)
    custom_author = models.CharField(max_length=300, blank=True)
    source_detection = models.ForeignKey(
        Detection, null=True, blank=True, on_delete=models.SET_NULL, related_name="library_entries"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def display_title(self) -> str:
        return self.custom_title or (self.book.title if self.book else "")

    @property
    def display_author(self) -> str:
        return self.custom_author or (self.book.author if self.book else "")

    def __str__(self):
        return self.display_title

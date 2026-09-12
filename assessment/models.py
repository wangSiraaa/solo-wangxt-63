from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from . import clock


class RoadGrid(models.Model):
    """道路网格：考核划分的空间单元。"""

    code = models.CharField("网格编码", max_length=32, unique=True)
    name = models.CharField("网格名称", max_length=128)
    geom = gis_models.MultiPolygonField("网格范围", srid=4326)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class Contractor(models.Model):
    """保洁承包商。"""

    code = models.CharField("承包商编码", max_length=32, unique=True)
    name = models.CharField("承包商名称", max_length=128)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class CleaningContract(models.Model):
    """保洁合同：某网格在 [valid_from, valid_to) 区间内由某承包商负责。

    扣分归属以“事件发生时”落在哪个合同区间为准，与录入时间无关。
    同一网格的合同区间不得重叠。
    """

    grid = models.ForeignKey(RoadGrid, on_delete=models.PROTECT, related_name="contracts", verbose_name="网格")
    contractor = models.ForeignKey(Contractor, on_delete=models.PROTECT, related_name="contracts", verbose_name="承包商")
    valid_from = models.DateTimeField("责任开始")
    valid_to = models.DateTimeField("责任结束", null=True, blank=True, help_text="为空表示长期有效")
    base_penalty = models.DecimalField("单起问题基础扣分", max_digits=10, decimal_places=2)
    overdue_daily_penalty = models.DecimalField("逾期每日加扣", max_digits=10, decimal_places=2, default=0)
    rectify_sla_hours = models.PositiveIntegerField("整改时限(小时)", default=24)

    class Meta:
        ordering = ["grid__code", "valid_from"]

    def clean(self):
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValidationError("责任结束时间必须晚于开始时间")
        if not self.grid_id:
            return
        overlapping = CleaningContract.objects.filter(grid_id=self.grid_id)
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)
        for other in overlapping:
            other_end = other.valid_to
            self_end = self.valid_to
            # 区间 [valid_from, valid_to) 相交判定（空端点视为正无穷）
            starts_before = other_end is None or self.valid_from < other_end
            ends_after = self_end is None or other.valid_from < self_end
            if starts_before and ends_after:
                raise ValidationError(f"与合同 #{other.pk} 的责任区间重叠")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.grid.code}/{self.contractor.code} @{self.valid_from:%Y-%m-%d}"


class ProblemEvent(models.Model):
    """问题事件：同一现场问题的一次发生。

    同一问题换角度拍摄的多张照片关联到同一事件，不重复计扣；
    整改完成后同一地点再次发生，属于新事件。
    """

    class Status(models.TextChoices):
        OPEN = "open", "待整改"
        RECTIFYING = "rectifying", "整改中"
        RECTIFIED = "rectified", "已整改"
        VOID = "void", "已作废"

    class Category(models.TextChoices):
        LITTER = "litter", "暴露垃圾"
        SPILL = "spill", "渣土遗撒"
        WATER = "water", "积水污渍"
        OTHER = "other", "其他"

    grid = models.ForeignKey(
        RoadGrid, on_delete=models.PROTECT, null=True, blank=True, related_name="events", verbose_name="所属网格"
    )
    category = models.CharField("问题类别", max_length=16, choices=Category.choices, default=Category.OTHER)
    location = gis_models.PointField("问题位置", srid=4326)
    occurred_at = models.DateTimeField("发生时间", help_text="取自首张证据照片的拍摄时间")
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.OPEN)
    rectification_deadline = models.DateTimeField("整改期限")
    rectified_at = models.DateTimeField("整改完成时间", null=True, blank=True)
    void_reason = models.CharField("作废原因", max_length=255, blank=True, default="")
    created_at = models.DateTimeField("录入时间", default=clock.now)

    class Meta:
        ordering = ["-occurred_at", "-id"]

    def __str__(self):
        return f"事件#{self.pk} {self.get_category_display()} {self.get_status_display()}"


class Photo(models.Model):
    """证据照片：保存原图、精确指纹与感知哈希。"""

    class Status(models.TextChoices):
        LINKED = "linked", "已关联事件"
        VOID = "void", "已作废"

    image = models.ImageField("照片", upload_to="evidence/%Y/%m/")
    sha256 = models.CharField("精确指纹", max_length=64, db_index=True)
    phash_hex = models.CharField("感知哈希", max_length=16)
    taken_at = models.DateTimeField("拍摄时间")
    location = gis_models.PointField("拍摄位置", srid=4326)
    event = models.ForeignKey(
        ProblemEvent, on_delete=models.PROTECT, null=True, blank=True, related_name="photos", verbose_name="关联事件"
    )
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.LINKED)
    uploaded_at = models.DateTimeField("上传时间", default=clock.now)

    class Meta:
        ordering = ["taken_at", "id"]

    def __str__(self):
        return f"照片#{self.pk} {self.sha256[:12]}"


class DuplicateCandidate(models.Model):
    """疑似重复候选：仅由哈希/精确指纹触发，供人工按位置和时间判断。"""

    class Status(models.TextChoices):
        PENDING = "pending", "待复核"
        CONFIRMED = "confirmed", "确认同一问题"
        REJECTED = "rejected", "确认非同一问题"

    photo_a = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name="candidates_as_a")
    photo_b = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name="candidates_as_b")
    exact_match = models.BooleanField("精确指纹一致")
    hamming_distance = models.PositiveSmallIntegerField("哈希汉明距离")
    distance_m = models.FloatField("拍摄点间距(米)")
    time_gap_seconds = models.BigIntegerField("拍摄时间差(秒)")
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.PENDING)
    resolution_note = models.CharField("复核意见", max_length=255, blank=True, default="")
    resolved_at = models.DateTimeField("复核时间", null=True, blank=True)
    created_at = models.DateTimeField("生成时间", default=clock.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["photo_a", "photo_b"], name="uniq_candidate_pair")]
        ordering = ["-id"]

    def __str__(self):
        return f"候选#{self.pk} 照片{self.photo_a_id}↔{self.photo_b_id} d={self.hamming_distance}"


class RectificationReport(models.Model):
    """整改回调：按 idempotency_key 幂等，重复回调不产生二次处理。"""

    event = models.ForeignKey(ProblemEvent, on_delete=models.PROTECT, related_name="rectification_reports")
    idempotency_key = models.CharField("幂等键", max_length=64, unique=True)
    reported_at = models.DateTimeField("整改上报时间")
    note = models.CharField("备注", max_length=255, blank=True, default="")
    received_at = models.DateTimeField("接收时间", default=clock.now)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"整改回调#{self.pk} 事件{self.event_id}"


class PenaltyUnit(models.Model):
    """处罚单元：每个问题事件至多一个有效处罚单元，所有扣分挂在其版本链上。"""

    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        VOID = "void", "已作废"

    event = models.OneToOneField(ProblemEvent, on_delete=models.PROTECT, related_name="penalty_unit")
    contract = models.ForeignKey(
        CleaningContract,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="penalty_units",
        verbose_name="归属合同",
        help_text="按事件发生时的责任区间确定",
    )
    contractor = models.ForeignKey(
        Contractor, on_delete=models.PROTECT, null=True, blank=True, related_name="penalty_units", verbose_name="归属承包商"
    )
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.ACTIVE)
    void_reason = models.CharField("作废原因", max_length=255, blank=True, default="")
    created_at = models.DateTimeField("创建时间", default=clock.now)

    class Meta:
        ordering = ["id"]

    def current_version(self):
        return self.versions.order_by("-version").first()

    def __str__(self):
        return f"处罚单元#{self.pk} 事件{self.event_id}"


class PenaltyVersion(models.Model):
    """处罚版本：追加式。复核通过后锁定，锁定后任何更正只能追加新版本。"""

    unit = models.ForeignKey(PenaltyUnit, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField("版本号")
    base_points = models.DecimalField("基础扣分", max_digits=10, decimal_places=2)
    escalation_points = models.DecimalField("逾期加扣", max_digits=10, decimal_places=2, default=0)
    reason = models.CharField("版本说明", max_length=255)
    locked = models.BooleanField("已锁定", default=False)
    locked_at = models.DateTimeField("锁定时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", default=clock.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["unit", "version"], name="uniq_penalty_version")]
        ordering = ["unit_id", "version"]

    @property
    def total_points(self):
        return self.base_points + self.escalation_points

    def save(self, *args, **kwargs):
        if self.pk is not None:
            db = PenaltyVersion.objects.get(pk=self.pk)
            if db.locked:
                raise ValidationError("处罚版本已锁定，只能追加新版本")
            immutable_changed = (
                db.unit_id != self.unit_id
                or db.version != self.version
                or db.base_points != self.base_points
                or db.escalation_points != self.escalation_points
                or db.reason != self.reason
            )
            if immutable_changed:
                raise ValidationError("处罚版本内容不可修改，只能追加新版本")
        super().save(*args, **kwargs)

    def lock(self):
        if self.locked:
            return
        self.locked = True
        self.locked_at = clock.now()
        self.save()

    def __str__(self):
        return f"处罚单元#{self.unit_id} v{self.version} 共{self.total_points}分"


# 供服务层使用的合同区间查询
def find_contract_for(grid, at):
    """返回 at 时刻对 grid 负责的合同（责任区间左闭右开）。"""
    if grid is None:
        return None
    return (
        CleaningContract.objects.filter(grid=grid, valid_from__lte=at)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at))
        .order_by("valid_from")
        .first()
    )

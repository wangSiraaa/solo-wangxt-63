"""业务规则服务层。

关键不变量：
1. 哈希只生成疑似重复候选；合并事件必须过位置/时间合理性校验并由人工确认。
2. 同一问题（同一事件）只有一条有效处罚单元，多角度照片不重复计扣。
3. 已整改事件不能被合并/关联——同一地点复发是新事件。
4. 扣分归属按事件发生时的合同责任区间。
5. 处罚版本追加式：复核通过即锁定，更正与逾期升级都只能追加新版本。
"""
import math
from datetime import timedelta

from django.conf import settings
from django.contrib.gis.geos import Point
from django.db import transaction
from rest_framework.exceptions import APIException

from . import clock, hashing
from .models import (
    DuplicateCandidate,
    PenaltyUnit,
    PenaltyVersion,
    Photo,
    ProblemEvent,
    RectificationReport,
    RoadGrid,
    find_contract_for,
)


class Conflict(APIException):
    status_code = 409
    default_detail = "业务状态冲突"
    default_code = "conflict"


def _max_link_distance_m():
    return settings.ASSESSMENT_MAX_LINK_DISTANCE_M


def _max_link_time_gap():
    return timedelta(hours=settings.ASSESSMENT_MAX_LINK_TIME_GAP_HOURS)


def make_point(lon, lat):
    return Point(float(lon), float(lat), srid=4326)


def assign_grid(location):
    return RoadGrid.objects.filter(geom__covers=location).order_by("id").first()


def spatial_distance_m(point_a, point_b):
    """用 PostGIS 大地线距离（米）。"""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT ST_Distance(%s::geography, %s::geography)",
            [point_a.ewkb.hex(), point_b.ewkb.hex()],
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------- 照片入库


@transaction.atomic
def ingest_photo(*, image_bytes, filename, taken_at, location, category=ProblemEvent.Category.OTHER):
    """保存照片、计算指纹与感知哈希、生成疑似重复候选，并为其开立问题事件。

    每张照片默认构成一个独立事件（并生成处罚单元）；若随后人工确认与既有
    事件是同一问题，则合并事件并作废多余处罚，保证不重复计扣。
    """
    sha = hashing.sha256_of(image_bytes)
    phash = hashing.perceptual_hash(image_bytes)
    photo = Photo.objects.create(
        sha256=sha,
        phash_hex=hashing.to_hex(phash),
        taken_at=taken_at,
        location=location,
        status=Photo.Status.LINKED,
    )
    from django.core.files.base import ContentFile

    photo.image.save(filename, ContentFile(image_bytes), save=True)
    _generate_candidates(photo)
    _create_event_for_photo(photo, category=category)
    return photo


def _generate_candidates(photo):
    """哈希只负责生成候选，不做任何合并决定。"""
    threshold = settings.ASSESSMENT_PHASH_THRESHOLD
    my_hash = hashing.from_hex(photo.phash_hex)
    others = Photo.objects.exclude(pk=photo.pk).exclude(status=Photo.Status.VOID)
    for other in others.iterator():
        exact = other.sha256 == photo.sha256
        distance = hashing.hamming_distance(my_hash, hashing.from_hex(other.phash_hex))
        if not exact and distance > threshold:
            continue
        a, b = (other, photo) if other.pk < photo.pk else (photo, other)
        if DuplicateCandidate.objects.filter(photo_a=a, photo_b=b).exists():
            continue
        DuplicateCandidate.objects.create(
            photo_a=a,
            photo_b=b,
            exact_match=exact,
            hamming_distance=distance,
            distance_m=spatial_distance_m(a.location, b.location),
            time_gap_seconds=int(abs((a.taken_at - b.taken_at).total_seconds())),
        )


def _create_event_for_photo(photo, category):
    grid = assign_grid(photo.location)
    contract = find_contract_for(grid, photo.taken_at)
    sla_hours = contract.rectify_sla_hours if contract else settings.ASSESSMENT_DEFAULT_SLA_HOURS
    event = ProblemEvent.objects.create(
        grid=grid,
        category=category,
        location=photo.location,
        occurred_at=photo.taken_at,
        rectification_deadline=photo.taken_at + timedelta(hours=sla_hours),
    )
    photo.event = event
    photo.save(update_fields=["event"])
    _create_penalty_unit(event, contract)
    return event


def _create_penalty_unit(event, contract):
    """按事件发生时间归属合同责任区间。"""
    unit = PenaltyUnit.objects.create(
        event=event,
        contract=contract,
        contractor=contract.contractor if contract else None,
    )
    PenaltyVersion.objects.create(
        unit=unit,
        version=1,
        base_points=contract.base_penalty if contract else 0,
        escalation_points=0,
        reason="初始核定",
    )
    return unit


# ---------------------------------------------------------------- 候选复核


def _assert_link_plausible(photo, event):
    """位置与时间合理性校验：防止把相似照片中的不同地点合并。"""
    if event.status == ProblemEvent.Status.RECTIFIED:
        raise Conflict("目标事件已整改完成，同一地点再次发生应作为新事件")
    if event.status == ProblemEvent.Status.VOID:
        raise Conflict("目标事件已作废")
    distance = spatial_distance_m(photo.location, event.location)
    if distance > _max_link_distance_m():
        raise Conflict(f"拍摄点相距 {distance:.0f} 米，超过 {_max_link_distance_m():.0f} 米上限，不能认定为同一问题")
    gap = abs((photo.taken_at - event.occurred_at).total_seconds())
    if gap > _max_link_time_gap().total_seconds():
        raise Conflict("拍摄时间与事件发生时间相差过大，不能认定为同一问题")


@transaction.atomic
def merge_events(*, target, source, reason):
    """把 source 并入 target：迁移照片、作废 source 及其处罚单元。"""
    if target.pk == source.pk:
        return target
    for status_holder, name in ((source, "被合并事件"), (target, "目标事件")):
        if status_holder.status == ProblemEvent.Status.RECTIFIED:
            raise Conflict(f"{name}已整改完成，不能合并；同一地点复发应作为新事件")
        if status_holder.status == ProblemEvent.Status.VOID:
            raise Conflict(f"{name}已作废，不能合并")
    for photo in source.photos.all():
        _assert_link_plausible(photo, target)
    source.photos.update(event=target)
    source.status = ProblemEvent.Status.VOID
    source.void_reason = reason
    source.save(update_fields=["status", "void_reason"])
    unit = PenaltyUnit.objects.filter(event=source).first()
    if unit and unit.status != PenaltyUnit.Status.VOID:
        unit.status = PenaltyUnit.Status.VOID
        unit.void_reason = reason
        unit.save(update_fields=["status", "void_reason"])
    if source.occurred_at < target.occurred_at:
        target.occurred_at = source.occurred_at
        target.save(update_fields=["occurred_at"])
    return target


@transaction.atomic
def confirm_candidate(candidate, note=""):
    """人工确认两张照片是同一问题：合并到同一事件，只保留一条处罚。"""
    if candidate.status != DuplicateCandidate.Status.PENDING:
        raise Conflict("该候选已复核")
    photo_new, photo_old = candidate.photo_b, candidate.photo_a
    event_a, event_b = photo_old.event, photo_new.event
    if event_a is not None and event_b is not None and event_a.pk != event_b.pk:
        target, source = (event_a, event_b) if event_a.occurred_at <= event_b.occurred_at else (event_b, event_a)
        merge_events(target=target, source=source, reason=f"候选#{candidate.pk}确认为同一问题")
    elif event_a is not None and event_b is None:
        _assert_link_plausible(photo_new, event_a)
        photo_new.event = event_a
        photo_new.save(update_fields=["event"])
    elif event_b is not None and event_a is None:
        _assert_link_plausible(photo_old, event_b)
        photo_old.event = event_b
        photo_old.save(update_fields=["event"])
    else:
        # 两者已在同一事件，仍需通过合理性校验（防止误确认跨地点同图）
        if event_a is not None:
            _assert_link_plausible(photo_new, event_a)
    candidate.status = DuplicateCandidate.Status.CONFIRMED
    candidate.resolution_note = note
    candidate.resolved_at = clock.now()
    candidate.save(update_fields=["status", "resolution_note", "resolved_at"])
    return candidate


@transaction.atomic
def reject_candidate(candidate, note="", void_newer_photo=False):
    """人工确认不是同一问题；误传场景可作废后传的照片及其孤立事件。"""
    if candidate.status != DuplicateCandidate.Status.PENDING:
        raise Conflict("该候选已复核")
    candidate.status = DuplicateCandidate.Status.REJECTED
    candidate.resolution_note = note
    candidate.resolved_at = clock.now()
    candidate.save(update_fields=["status", "resolution_note", "resolved_at"])
    if void_newer_photo:
        photo = max((candidate.photo_a, candidate.photo_b), key=lambda p: p.uploaded_at)
        _void_photo(photo, reason=note or "误传作废")
    return candidate


def _void_photo(photo, reason):
    photo.status = Photo.Status.VOID
    photo.save(update_fields=["status"])
    event = photo.event
    if event is None:
        return
    if event.photos.exclude(status=Photo.Status.VOID).exists():
        return  # 事件还有其他有效照片，不能连带作废
    event.status = ProblemEvent.Status.VOID
    event.void_reason = reason
    event.save(update_fields=["status", "void_reason"])
    unit = PenaltyUnit.objects.filter(event=event).first()
    if unit and unit.status != PenaltyUnit.Status.VOID:
        unit.status = PenaltyUnit.Status.VOID
        unit.void_reason = reason
        unit.save(update_fields=["status", "void_reason"])


@transaction.atomic
def link_photo_to_event(photo, event):
    """人工把照片关联到既有事件（哈希未命中时的兜底），同样过合理性校验。"""
    if photo.status == Photo.Status.VOID:
        raise Conflict("照片已作废")
    if photo.event_id == event.id:
        return photo
    _assert_link_plausible(photo, event)
    old_event = photo.event
    photo.event = event
    photo.save(update_fields=["event"])
    if old_event is not None and not old_event.photos.exists():
        old_event.status = ProblemEvent.Status.VOID
        old_event.void_reason = f"照片#{photo.pk} 改挂事件#{event.pk}，原事件作废"
        old_event.save(update_fields=["status", "void_reason"])
        unit = PenaltyUnit.objects.filter(event=old_event).first()
        if unit and unit.status != PenaltyUnit.Status.VOID:
            unit.status = PenaltyUnit.Status.VOID
            unit.void_reason = old_event.void_reason
            unit.save(update_fields=["status", "void_reason"])
    return photo


# ---------------------------------------------------------------- 整改回调


@transaction.atomic
def report_rectification(*, event, idempotency_key, reported_at, note=""):
    """幂等整改回调：相同幂等键重复上报返回原记录，不重复改状态。"""
    existing = RectificationReport.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing, False
    if event.status == ProblemEvent.Status.VOID:
        raise Conflict("事件已作废")
    if event.status == ProblemEvent.Status.RECTIFIED:
        raise Conflict("事件已整改完成，重复回调请使用原幂等键")
    report = RectificationReport.objects.create(
        event=event, idempotency_key=idempotency_key, reported_at=reported_at, note=note
    )
    event.status = ProblemEvent.Status.RECTIFIED
    event.rectified_at = reported_at
    event.save(update_fields=["status", "rectified_at"])
    return report, True


# ---------------------------------------------------------------- 处罚复核与更正


@transaction.atomic
def approve_penalty(unit):
    """复核通过：锁定当前处罚版本。"""
    if unit.status != PenaltyUnit.Status.ACTIVE:
        raise Conflict("处罚单元已作废")
    version = unit.current_version()
    version.lock()
    return version


@transaction.atomic
def correct_penalty(unit, *, reason, base_points=None, escalation_points=None):
    """更正只能追加新版本，历史版本（含已锁定）保持不变。"""
    if unit.status != PenaltyUnit.Status.ACTIVE:
        raise Conflict("处罚单元已作废")
    if not reason:
        raise Conflict("更正必须填写原因")
    current = unit.current_version()
    return PenaltyVersion.objects.create(
        unit=unit,
        version=current.version + 1,
        base_points=current.base_points if base_points is None else base_points,
        escalation_points=current.escalation_points if escalation_points is None else escalation_points,
        reason=reason,
    )


# ---------------------------------------------------------------- 逾期升级


@transaction.atomic
def run_escalation(now=None):
    """对逾期未整改事件按合同加扣，追加新处罚版本。

    基于可注入时钟；同一逾期天数重复执行不会产生重复版本（幂等）。
    """
    now = now or clock.now()
    created = []
    events = ProblemEvent.objects.filter(
        status__in=[ProblemEvent.Status.OPEN, ProblemEvent.Status.RECTIFYING],
        rectification_deadline__lt=now,
    ).select_related("penalty_unit__contract")
    for event in events:
        unit = PenaltyUnit.objects.filter(event=event, status=PenaltyUnit.Status.ACTIVE).first()
        if unit is None or unit.contract is None:
            continue
        overdue_seconds = (now - event.rectification_deadline).total_seconds()
        overdue_days = math.ceil(overdue_seconds / 86400)
        escalation = unit.contract.overdue_daily_penalty * overdue_days
        current = unit.current_version()
        if escalation <= current.escalation_points:
            continue
        created.append(
            PenaltyVersion.objects.create(
                unit=unit,
                version=current.version + 1,
                base_points=current.base_points,
                escalation_points=escalation,
                reason=f"逾期{overdue_days}天升级",
            )
        )
    return created

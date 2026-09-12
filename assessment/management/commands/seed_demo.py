"""载入演示数据：网格、承包商、合同，并用模拟照片走一遍完整业务流程。

演示路径：
1. 网格A 出现垃圾（garbage_a.png）→ 事件1 + 处罚1
2. 同一问题换角度再拍（garbage_a_angle2.jpg）→ 候选确认合并，不重复计扣
3. 同图误传到网格B（garbage_a_copy.png）→ 候选驳回并作废误传
4. 事件1 整改回调（幂等）
5. 同地点复发（garbage_b.png）→ 新事件2 + 处罚2
"""
from datetime import timezone

from django.core.management.base import BaseCommand

from assessment import services
from assessment.models import (
    CleaningContract,
    Contractor,
    DuplicateCandidate,
    PenaltyUnit,
    ProblemEvent,
    RoadGrid,
)
from assessment.tests.base import GRID_A, GRID_B, dt, square
from assessment.tests.helpers import scene_bytes, variant_bytes

UTC = timezone.utc


class Command(BaseCommand):
    help = "载入环卫考核演示数据"

    def handle(self, *args, **options):
        grid_a, _ = RoadGrid.objects.get_or_create(
            code="GD-A", defaults={"name": "东街网格", "geom": square(116.30, 39.98, 116.32, 40.00)}
        )
        grid_b, _ = RoadGrid.objects.get_or_create(
            code="GD-B", defaults={"name": "西街网格", "geom": square(116.60, 39.98, 116.62, 40.00)}
        )
        jia, _ = Contractor.objects.get_or_create(code="C-JIA", defaults={"name": "甲保洁公司"})
        bing, _ = Contractor.objects.get_or_create(code="C-BING", defaults={"name": "丙保洁公司"})
        CleaningContract.objects.get_or_create(
            grid=grid_a,
            contractor=jia,
            valid_from=dt(2026, 1, 1),
            defaults={"base_penalty": 50, "overdue_daily_penalty": 10, "rectify_sla_hours": 24},
        )
        CleaningContract.objects.get_or_create(
            grid=grid_b,
            contractor=bing,
            valid_from=dt(2026, 1, 1),
            defaults={"base_penalty": 80, "overdue_daily_penalty": 20, "rectify_sla_hours": 24},
        )

        img_a = scene_bytes(1)
        # 1) 网格A 出现垃圾
        p1 = services.ingest_photo(
            image_bytes=img_a, filename="garbage_a.png", taken_at=dt(2026, 3, 1, 8),
            location=services.make_point(**GRID_A), category="litter",
        )
        # 2) 同一问题换角度 → 候选确认合并
        p2 = services.ingest_photo(
            image_bytes=variant_bytes(img_a), filename="garbage_a_angle2.jpg",
            taken_at=dt(2026, 3, 1, 8, 20), location=services.make_point(lon=116.31005, lat=39.990),
            category="litter",
        )
        candidate = DuplicateCandidate.objects.order_by("-id").first()
        services.confirm_candidate(candidate, note="同一堆垃圾，不同角度")
        # 3) 同图误传到网格B → 候选驳回并作废
        p3 = services.ingest_photo(
            image_bytes=img_a, filename="garbage_a_copy.png", taken_at=dt(2026, 3, 1, 9),
            location=services.make_point(**GRID_B), category="litter",
        )
        misreport = DuplicateCandidate.objects.order_by("-id").first()
        services.reject_candidate(misreport, note="同图跨地点误传", void_newer_photo=True)
        # 4) 事件1 整改回调（幂等演示：重复上报同键）
        event1 = p1.event
        services.report_rectification(event=event1, idempotency_key="demo-cb-1", reported_at=dt(2026, 3, 1, 12))
        services.report_rectification(event=event1, idempotency_key="demo-cb-1", reported_at=dt(2026, 3, 1, 12))
        # 5) 同地点复发 → 新事件
        p4 = services.ingest_photo(
            image_bytes=scene_bytes(2), filename="garbage_b.png", taken_at=dt(2026, 3, 3, 8),
            location=services.make_point(lon=116.31003, lat=39.990), category="litter",
        )

        self.stdout.write(self.style.SUCCESS("演示数据已载入："))
        for unit in PenaltyUnit.objects.select_related("event", "contractor"):
            current = unit.current_version()
            self.stdout.write(
                f"  处罚单元#{unit.pk} 事件{unit.event_id}({unit.event.status}) "
                f"承包商={unit.contractor and unit.contractor.name} 状态={unit.status} "
                f"当前扣分={current.total_points if current else '-'}"
            )
        self.stdout.write(f"  事件总数={ProblemEvent.objects.count()} 候选总数={DuplicateCandidate.objects.count()}")

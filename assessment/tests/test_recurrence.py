"""同地点复发与幂等整改回调。"""
from assessment.models import PenaltyUnit, Photo, ProblemEvent, RectificationReport
from assessment.tests.base import GRID_A, AssessmentTestCase, dt
from assessment.tests.helpers import scene_bytes


class RecurrenceAfterRectificationTest(AssessmentTestCase):
    """整改完成后同一地点再次发生 = 新事件、新处罚，不与旧事件合并。"""

    def test_recurrence_creates_new_event_and_penalty(self):
        _, p1 = self.upload_photo(scene_bytes(1), dt(2026, 3, 1, 8), **GRID_A)
        event1_id = p1["event"]

        # 整改回调：事件 1 闭环
        resp = self.api.post(
            f"/api/events/{event1_id}/rectification-callback/",
            {"idempotency_key": "rectify-1", "reported_at": dt(2026, 3, 1, 10).isoformat()},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        event1 = ProblemEvent.objects.get(pk=event1_id)
        self.assertEqual(event1.status, ProblemEvent.Status.RECTIFIED)

        # 两天后同一地点（约 3 米内）又出现垃圾：新照片自动开新事件
        _, p2 = self.upload_photo(scene_bytes(2), dt(2026, 3, 3, 8), lon=116.31003, lat=39.990)
        event2_id = p2["event"]
        self.assertNotEqual(event1_id, event2_id)

        # 人工也无法把新照片挂到已整改的旧事件上
        resp = self.api.post(f"/api/photos/{p2['id']}/link/", {"event_id": event1_id}, format="json")
        self.assertEqual(resp.status_code, 409)

        # 两起事件各有一条有效处罚，分别可追溯
        active = PenaltyUnit.objects.filter(status=PenaltyUnit.Status.ACTIVE).order_by("id")
        self.assertEqual(active.count(), 2)
        self.assertEqual({u.event_id for u in active}, {event1_id, event2_id})
        for unit in active:
            detail = self.api.get(f"/api/penalties/{unit.id}/").json()
            self.assertEqual(detail["event"], unit.event_id)
            self.assertEqual(len(detail["evidence"]), 1)
            self.assertEqual(detail["current_total"], "50.00")


class DuplicateRectificationCallbackTest(AssessmentTestCase):
    """重复整改回调：同一幂等键只处理一次。"""

    def test_idempotent_callback(self):
        _, p1 = self.upload_photo(scene_bytes(1), dt(2026, 3, 1, 8), **GRID_A)
        event_id = p1["event"]
        payload = {"idempotency_key": "cb-001", "reported_at": dt(2026, 3, 1, 12).isoformat(), "note": "已清理"}

        resp1 = self.api.post(f"/api/events/{event_id}/rectification-callback/", payload, format="json")
        self.assertEqual(resp1.status_code, 201)
        report_id = resp1.json()["id"]

        # 网络重试/重复回调：返回同一条记录，不产生二次处理
        resp2 = self.api.post(f"/api/events/{event_id}/rectification-callback/", payload, format="json")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["id"], report_id)
        self.assertEqual(RectificationReport.objects.count(), 1)

        event = ProblemEvent.objects.get(pk=event_id)
        self.assertEqual(event.status, ProblemEvent.Status.RECTIFIED)
        self.assertEqual(event.rectified_at, dt(2026, 3, 1, 12))

        # 已整改事件收到“不同幂等键”的回调 → 冲突，不覆盖既有整改时间
        resp3 = self.api.post(
            f"/api/events/{event_id}/rectification-callback/",
            {"idempotency_key": "cb-002", "reported_at": dt(2026, 3, 1, 18).isoformat()},
            format="json",
        )
        self.assertEqual(resp3.status_code, 409)
        event.refresh_from_db()
        self.assertEqual(event.rectified_at, dt(2026, 3, 1, 12))
        self.assertEqual(RectificationReport.objects.count(), 1)

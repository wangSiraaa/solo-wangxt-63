"""扣分归属、逾期升级（可注入时钟）、复核锁定与追加式更正。"""
from django.core.exceptions import ValidationError

from assessment import clock
from assessment.models import PenaltyUnit, PenaltyVersion
from assessment.tests.base import GRID_A, AssessmentTestCase, dt
from assessment.tests.helpers import scene_bytes


class AttributionTest(AssessmentTestCase):
    """扣分归属按事件发生时的合同责任区间，不按录入时的承包商。"""

    def test_attribution_by_occurrence_time(self):
        # 录入时间已是乙的合同期（2026-06-05）
        with clock.freeze_time(dt(2026, 6, 5, 9)):
            # 事件发生在 5 月 30 日 → 甲的合同区间
            _, p1 = self.upload_photo(scene_bytes(1), dt(2026, 5, 30, 8), **GRID_A)
            # 事件发生在 6 月 3 日 → 乙的合同区间
            _, p2 = self.upload_photo(scene_bytes(2), dt(2026, 6, 3, 8), **GRID_A)

        unit1 = PenaltyUnit.objects.get(event_id=p1["event"])
        unit2 = PenaltyUnit.objects.get(event_id=p2["event"])
        self.assertEqual(unit1.contract, self.contract_a1)
        self.assertEqual(unit1.contractor, self.con_jia)
        self.assertEqual(unit1.current_version().base_points, 50)
        self.assertEqual(unit2.contract, self.contract_a2)
        self.assertEqual(unit2.contractor, self.con_yi)
        self.assertEqual(unit2.current_version().base_points, 60)


class EscalationTest(AssessmentTestCase):
    """逾期升级基于可注入时钟，按逾期天数追加版本且幂等。"""

    def test_escalation_with_injected_clock(self):
        with clock.freeze_time(dt(2026, 3, 1, 8)):
            _, p1 = self.upload_photo(scene_bytes(1), dt(2026, 3, 1, 8), **GRID_A)
        unit = PenaltyUnit.objects.get(event_id=p1["event"])
        event = unit.event
        self.assertEqual(event.rectification_deadline, dt(2026, 3, 2, 8))

        # 未到期限：不产生升级
        with clock.freeze_time(dt(2026, 3, 2, 7, 59)):
            resp = self.api.post("/api/escalations/run/")
        self.assertEqual(resp.json()["created_versions"], [])

        # 逾期 49 小时 → 按 3 天计，加扣 3*10=30
        with clock.freeze_time(dt(2026, 3, 4, 9)):
            resp = self.api.post("/api/escalations/run/")
            created = resp.json()["created_versions"]
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0]["escalation_points"], "30.00")
            self.assertEqual(created[0]["version"], 2)
            # 同一时刻重复执行：幂等，不再追加
            resp = self.api.post("/api/escalations/run/")
            self.assertEqual(resp.json()["created_versions"], [])

        # 复核锁定 v2 后，逾期继续加深 → 仍只能追加新版本
        self.api.post(f"/api/penalties/{unit.id}/approve-review/")
        with clock.freeze_time(dt(2026, 3, 5, 9)):
            resp = self.api.post("/api/escalations/run/")
            created = resp.json()["created_versions"]
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0]["escalation_points"], "40.00")
            self.assertEqual(created[0]["version"], 3)

        versions = list(unit.versions.order_by("version"))
        self.assertEqual([v.version for v in versions], [1, 2, 3])
        self.assertTrue(versions[1].locked)
        self.assertFalse(versions[2].locked)
        self.assertEqual(versions[1].escalation_points, 30)  # 锁定版本不被改动


class LockAndCorrectionTest(AssessmentTestCase):
    """复核通过锁定处罚版本；更正只能追加，历史版本不可改。"""

    def test_approve_review_response_shows_locked_version(self):
        """approve-review 响应内当前版本必须是 locked:true，且与 GET 一致。"""
        _, p1 = self.upload_photo(scene_bytes(1), dt(2026, 3, 1, 8), **GRID_A)
        unit = PenaltyUnit.objects.get(event_id=p1["event"])

        resp = self.api.post(f"/api/penalties/{unit.id}/approve-review/")
        self.assertEqual(resp.status_code, 200)
        current = max(resp.json()["versions"], key=lambda v: v["version"])
        self.assertTrue(current["locked"])
        self.assertIsNotNone(current["locked_at"])

        # GET 同一资源，与 POST 响应一致
        detail = self.api.get(f"/api/penalties/{unit.id}/").json()
        current = max(detail["versions"], key=lambda v: v["version"])
        self.assertTrue(current["locked"])
        self.assertEqual(detail["versions"], resp.json()["versions"])

    def test_lock_and_append_only_correction(self):
        _, p1 = self.upload_photo(scene_bytes(1), dt(2026, 3, 1, 8), **GRID_A)
        unit = PenaltyUnit.objects.get(event_id=p1["event"])

        # 复核通过 → v1 锁定
        resp = self.api.post(f"/api/penalties/{unit.id}/approve-review/")
        self.assertEqual(resp.status_code, 200)
        v1 = unit.versions.get(version=1)
        self.assertTrue(v1.locked)
        self.assertIsNotNone(v1.locked_at)

        # 锁定版本在模型层也改不动
        v1.base_points = 999
        with self.assertRaises(ValidationError):
            v1.save()

        # 更正只能追加：v2 生效，v1 原样保留
        resp = self.api.post(
            f"/api/penalties/{unit.id}/correct/",
            {"reason": "现场复核扣分标准适用错误", "base_points": "30.00"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        versions = list(unit.versions.order_by("version"))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].base_points, 50)
        self.assertTrue(versions[0].locked)
        self.assertEqual(versions[1].base_points, 30)
        self.assertFalse(versions[1].locked)

        # 更正必须填原因
        resp = self.api.post(f"/api/penalties/{unit.id}/correct/", {"base_points": "10.00"}, format="json")
        self.assertEqual(resp.status_code, 400)

        # 每笔扣分可追到唯一处罚单元与证据
        detail = self.api.get(f"/api/penalties/{unit.id}/").json()
        self.assertEqual(detail["event"], p1["event"])
        self.assertEqual(detail["contract"], self.contract_a1.id)
        self.assertEqual(detail["contractor"], self.con_jia.id)
        self.assertEqual([v["version"] for v in detail["versions"]], [1, 2])
        self.assertEqual(detail["evidence"][0]["sha256"], p1["sha256"])
        self.assertEqual(detail["current_total"], "30.00")

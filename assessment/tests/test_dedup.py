"""疑似重复候选与事件关联：哈希只提示，位置/时间/人工判断决定。"""
from assessment.models import DuplicateCandidate, PenaltyUnit, Photo, ProblemEvent
from assessment.tests.base import GRID_A, GRID_B, AssessmentTestCase, dt
from assessment.tests.helpers import scene_bytes, variant_bytes


class SameImageCrossLocationTest(AssessmentTestCase):
    """同图跨地点误传：精确指纹命中候选，但距离超限，人工驳回并作废误传。"""

    def test_same_image_different_location_rejected(self):
        img = scene_bytes(1)
        _, p1 = self.upload_photo(img, dt(2026, 3, 1, 8), **GRID_A)
        # 同一张图被误传到 26km 外的网格 B
        _, p2 = self.upload_photo(img, dt(2026, 3, 1, 8, 5), **GRID_B)

        candidate = DuplicateCandidate.objects.get()
        self.assertTrue(candidate.exact_match)
        self.assertEqual(candidate.hamming_distance, 0)
        self.assertGreater(candidate.distance_m, 10_000)

        # 距离超限，人工也无法确认是同一问题
        resp = self.api.post(f"/api/candidates/{candidate.id}/confirm/", {"note": "想合并"}, format="json")
        self.assertEqual(resp.status_code, 409)
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, DuplicateCandidate.Status.PENDING)

        # 驳回并按误传作废后传的照片：其孤立事件与处罚一并作废
        resp = self.api.post(
            f"/api/candidates/{candidate.id}/reject/",
            {"note": "同图跨地点误传", "void_newer_photo": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        photo2 = Photo.objects.get(pk=p2["id"])
        self.assertEqual(photo2.status, Photo.Status.VOID)
        event2 = photo2.event
        self.assertEqual(event2.status, ProblemEvent.Status.VOID)
        self.assertEqual(event2.penalty_unit.status, PenaltyUnit.Status.VOID)

        # 全系统只剩网格 A 那一条有效扣分，且能追到证据
        active = PenaltyUnit.objects.filter(status=PenaltyUnit.Status.ACTIVE)
        self.assertEqual(active.count(), 1)
        unit = active.get()
        self.assertEqual(unit.contract, self.contract_a1)
        self.assertEqual(unit.contractor, self.con_jia)
        detail = self.api.get(f"/api/penalties/{unit.id}/").json()
        self.assertEqual(detail["event"], p1["event"])
        self.assertEqual([e["sha256"] for e in detail["evidence"]], [p1["sha256"]])


class SameProblemDifferentAngleTest(AssessmentTestCase):
    """同一问题换角度拍摄：确认合并后不重复计扣。"""

    def test_merge_keeps_single_penalty(self):
        img = scene_bytes(1)
        angle2 = variant_bytes(img)
        _, p1 = self.upload_photo(img, dt(2026, 3, 1, 8), **GRID_A)
        # 同一堆垃圾，换个角度，位置差约 5 米，晚 20 分钟
        _, p2 = self.upload_photo(angle2, dt(2026, 3, 1, 8, 20), lon=116.31005, lat=39.990, filename="angle2.jpg")

        candidate = DuplicateCandidate.objects.get()
        self.assertFalse(candidate.exact_match)
        self.assertLessEqual(candidate.hamming_distance, 10)
        self.assertLess(candidate.distance_m, 150)

        # 复核前确实各开了一条处罚（待人工判断）
        self.assertEqual(PenaltyUnit.objects.filter(status=PenaltyUnit.Status.ACTIVE).count(), 2)

        resp = self.api.post(f"/api/candidates/{candidate.id}/confirm/", {"note": "同一堆垃圾"}, format="json")
        self.assertEqual(resp.status_code, 200)

        event1 = ProblemEvent.objects.get(pk=p1["event"])
        event2 = ProblemEvent.objects.get(pk=p2["event"])
        self.assertEqual(event2.status, ProblemEvent.Status.VOID)
        self.assertEqual(event2.penalty_unit.status, PenaltyUnit.Status.VOID)
        self.assertEqual(event1.photos.count(), 2)

        # 只剩一条有效处罚单元，两张照片都是它的证据
        unit = PenaltyUnit.objects.get(status=PenaltyUnit.Status.ACTIVE)
        self.assertEqual(unit.event, event1)
        detail = self.api.get(f"/api/penalties/{unit.id}/").json()
        self.assertEqual(len(detail["evidence"]), 2)
        self.assertEqual(detail["current_total"], "50.00")


class SimilarPhotoDifferentLocationTest(AssessmentTestCase):
    """相似照片但不同地点：哈希再像也不能合并。"""

    def test_similar_hash_far_apart_cannot_merge(self):
        img = scene_bytes(1)
        lookalike = variant_bytes(img)
        self.upload_photo(img, dt(2026, 3, 1, 8), **GRID_A)
        # 不同地点（网格 B）出现观感相似的照片
        self.upload_photo(lookalike, dt(2026, 3, 1, 9), **GRID_B, filename="lookalike.jpg")

        candidate = DuplicateCandidate.objects.get()
        self.assertLessEqual(candidate.hamming_distance, 10)
        self.assertGreater(candidate.distance_m, 10_000)

        resp = self.api.post(f"/api/candidates/{candidate.id}/confirm/", {}, format="json")
        self.assertEqual(resp.status_code, 409)

        # 两条处罚都保持有效，互不合并
        self.assertEqual(PenaltyUnit.objects.filter(status=PenaltyUnit.Status.ACTIVE).count(), 2)

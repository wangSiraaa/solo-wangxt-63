"""测试基类：网格/承包商/合同 fixture 与照片上传辅助。"""
import shutil
import tempfile
from datetime import datetime, timezone

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from assessment.models import CleaningContract, Contractor, RoadGrid

UTC = timezone.utc


def dt(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def square(lon_min, lat_min, lon_max, lat_max):
    return MultiPolygon(
        Polygon(((lon_min, lat_min), (lon_max, lat_min), (lon_max, lat_max), (lon_min, lat_max), (lon_min, lat_min))),
        srid=4326,
    )


# 网格 A：约 lon 116.300~116.320, lat 39.980~40.000；网格 B 在约 26km 外
GRID_A = dict(lon=116.310, lat=39.990)
GRID_B = dict(lon=116.610, lat=39.990)


class AssessmentTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp(prefix="evidence-")
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.grid_a = RoadGrid.objects.create(code="GD-A", name="东街网格", geom=square(116.30, 39.98, 116.32, 40.00))
        cls.grid_b = RoadGrid.objects.create(code="GD-B", name="西街网格", geom=square(116.60, 39.98, 116.62, 40.00))
        cls.con_jia = Contractor.objects.create(code="C-JIA", name="甲保洁公司")
        cls.con_yi = Contractor.objects.create(code="C-YI", name="乙保洁公司")
        cls.con_bing = Contractor.objects.create(code="C-BING", name="丙保洁公司")
        # 网格 A：甲负责到 2026-06-01，之后乙接手（责任区间左闭右开）
        cls.contract_a1 = CleaningContract.objects.create(
            grid=cls.grid_a,
            contractor=cls.con_jia,
            valid_from=dt(2026, 1, 1),
            valid_to=dt(2026, 6, 1),
            base_penalty=50,
            overdue_daily_penalty=10,
            rectify_sla_hours=24,
        )
        cls.contract_a2 = CleaningContract.objects.create(
            grid=cls.grid_a,
            contractor=cls.con_yi,
            valid_from=dt(2026, 6, 1),
            valid_to=None,
            base_penalty=60,
            overdue_daily_penalty=12,
            rectify_sla_hours=24,
        )
        cls.contract_b1 = CleaningContract.objects.create(
            grid=cls.grid_b,
            contractor=cls.con_bing,
            valid_from=dt(2026, 1, 1),
            valid_to=None,
            base_penalty=80,
            overdue_daily_penalty=20,
            rectify_sla_hours=24,
        )

    def setUp(self):
        self.api = APIClient()

    def upload_photo(self, image_bytes, taken_at, lon, lat, category="litter", filename="scene.png"):
        """通过 API 上传照片，返回 (response, json)。"""
        from django.core.files.uploadedfile import SimpleUploadedFile

        fmt = "png" if filename.endswith(".png") else "jpeg"
        upload = SimpleUploadedFile(filename, image_bytes, content_type=f"image/{fmt}")
        resp = self.api.post(
            "/api/photos/",
            {
                "image": upload,
                "taken_at": taken_at.isoformat(),
                "lon": lon,
                "lat": lat,
                "category": category,
            },
            format="multipart",
        )
        assert resp.status_code == 201, resp.content
        return resp, resp.json()

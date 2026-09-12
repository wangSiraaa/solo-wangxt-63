import json

from django.contrib.gis.geos import GEOSGeometry
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    CleaningContract,
    Contractor,
    DuplicateCandidate,
    PenaltyUnit,
    PenaltyVersion,
    Photo,
    ProblemEvent,
    RectificationReport,
    RoadGrid,
)


class RoadGridSerializer(serializers.ModelSerializer):
    geom = serializers.JSONField(help_text="GeoJSON Polygon/MultiPolygon")

    class Meta:
        model = RoadGrid
        fields = ["id", "code", "name", "geom"]

    def validate_geom(self, value):
        from django.contrib.gis.geos import MultiPolygon

        try:
            geom = GEOSGeometry(json.dumps(value))
        except Exception as exc:
            raise serializers.ValidationError(f"非法 GeoJSON: {exc}")
        if geom.geom_type == "Polygon":
            geom = MultiPolygon(geom, srid=4326)
        elif geom.geom_type != "MultiPolygon":
            raise serializers.ValidationError("仅支持 Polygon/MultiPolygon")
        if geom.srid is None:
            geom.srid = 4326
        return geom

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["geom"] = json.loads(instance.geom.geojson)
        return data


class ContractorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contractor
        fields = ["id", "code", "name"]


class CleaningContractSerializer(serializers.ModelSerializer):
    class Meta:
        model = CleaningContract
        fields = [
            "id",
            "grid",
            "contractor",
            "valid_from",
            "valid_to",
            "base_penalty",
            "overdue_daily_penalty",
            "rectify_sla_hours",
        ]

    def validate(self, attrs):
        from django.core.exceptions import ValidationError as DjangoValidationError

        instance = self.instance or CleaningContract()
        for key, value in attrs.items():
            setattr(instance, key, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return attrs


class PhotoSerializer(serializers.ModelSerializer):
    lat = serializers.FloatField(write_only=True)
    lon = serializers.FloatField(write_only=True)
    category = serializers.ChoiceField(
        choices=ProblemEvent.Category.choices, write_only=True, required=False, default=ProblemEvent.Category.OTHER
    )
    location = serializers.SerializerMethodField()

    class Meta:
        model = Photo
        fields = [
            "id",
            "image",
            "sha256",
            "phash_hex",
            "taken_at",
            "lat",
            "lon",
            "category",
            "location",
            "event",
            "status",
            "uploaded_at",
        ]
        read_only_fields = ["sha256", "phash_hex", "event", "status", "uploaded_at"]

    @extend_schema_field({"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}})
    def get_location(self, obj):
        return {"lat": obj.location.y, "lon": obj.location.x}


class DuplicateCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DuplicateCandidate
        fields = [
            "id",
            "photo_a",
            "photo_b",
            "exact_match",
            "hamming_distance",
            "distance_m",
            "time_gap_seconds",
            "status",
            "resolution_note",
            "resolved_at",
            "created_at",
        ]
        read_only_fields = fields


class ConfirmCandidateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class RejectCandidateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    void_newer_photo = serializers.BooleanField(
        required=False, default=False, help_text="误传场景：作废后上传的照片及其孤立事件/处罚"
    )


class RectificationReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = RectificationReport
        fields = ["id", "event", "idempotency_key", "reported_at", "note", "received_at"]
        read_only_fields = ["event", "received_at"]
        # 幂等键的唯一性由服务层处理（重复回调返回原记录而非 400）
        extra_kwargs = {"idempotency_key": {"validators": []}}


class ProblemEventSerializer(serializers.ModelSerializer):
    location = serializers.SerializerMethodField()
    photo_ids = serializers.SerializerMethodField()
    penalty_unit_id = serializers.SerializerMethodField()

    class Meta:
        model = ProblemEvent
        fields = [
            "id",
            "grid",
            "category",
            "location",
            "occurred_at",
            "status",
            "rectification_deadline",
            "rectified_at",
            "void_reason",
            "created_at",
            "photo_ids",
            "penalty_unit_id",
        ]
        read_only_fields = [
            "grid",
            "location",
            "occurred_at",
            "status",
            "rectification_deadline",
            "rectified_at",
            "void_reason",
            "created_at",
        ]

    @extend_schema_field({"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}})
    def get_location(self, obj):
        return {"lat": obj.location.y, "lon": obj.location.x}

    @extend_schema_field({"type": "array", "items": {"type": "integer"}})
    def get_photo_ids(self, obj):
        return list(obj.photos.values_list("id", flat=True))

    @extend_schema_field({"type": "integer", "nullable": True})
    def get_penalty_unit_id(self, obj):
        unit = getattr(obj, "penalty_unit", None)
        return unit.pk if unit else None


class PenaltyVersionSerializer(serializers.ModelSerializer):
    total_points = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PenaltyVersion
        fields = [
            "id",
            "version",
            "base_points",
            "escalation_points",
            "total_points",
            "reason",
            "locked",
            "locked_at",
            "created_at",
        ]
        read_only_fields = fields


class EvidencePhotoSerializer(serializers.ModelSerializer):
    location = serializers.SerializerMethodField()

    class Meta:
        model = Photo
        fields = ["id", "image", "sha256", "phash_hex", "taken_at", "location", "status"]

    @extend_schema_field({"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}})
    def get_location(self, obj):
        return {"lat": obj.location.y, "lon": obj.location.x}


class PenaltyUnitSerializer(serializers.ModelSerializer):
    versions = PenaltyVersionSerializer(many=True, read_only=True)
    evidence = serializers.SerializerMethodField(help_text="事件下全部证据照片")
    current_total = serializers.SerializerMethodField()

    class Meta:
        model = PenaltyUnit
        fields = [
            "id",
            "event",
            "contract",
            "contractor",
            "status",
            "void_reason",
            "created_at",
            "current_total",
            "versions",
            "evidence",
        ]
        read_only_fields = fields

    @extend_schema_field(EvidencePhotoSerializer(many=True))
    def get_evidence(self, obj):
        return EvidencePhotoSerializer(obj.event.photos.all(), many=True, context=self.context).data

    @extend_schema_field({"type": "string", "nullable": True})
    def get_current_total(self, obj):
        from decimal import Decimal

        version = obj.current_version()
        return str(version.total_points.quantize(Decimal("0.01"))) if version else None


class ApprovePenaltySerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CorrectPenaltySerializer(serializers.Serializer):
    reason = serializers.CharField(help_text="更正原因（必填，追加新版本）")
    base_points = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    escalation_points = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)


class LinkPhotoSerializer(serializers.Serializer):
    event_id = serializers.IntegerField()


class EscalationResultSerializer(serializers.Serializer):
    created_versions = PenaltyVersionSerializer(many=True)

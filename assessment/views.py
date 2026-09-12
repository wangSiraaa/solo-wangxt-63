from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import (
    CleaningContract,
    Contractor,
    DuplicateCandidate,
    PenaltyUnit,
    Photo,
    ProblemEvent,
    RoadGrid,
)
from .serializers import (
    ApprovePenaltySerializer,
    CleaningContractSerializer,
    ConfirmCandidateSerializer,
    ContractorSerializer,
    CorrectPenaltySerializer,
    DuplicateCandidateSerializer,
    EscalationResultSerializer,
    LinkPhotoSerializer,
    PenaltyUnitSerializer,
    PhotoSerializer,
    ProblemEventSerializer,
    RectificationReportSerializer,
    RejectCandidateSerializer,
    RoadGridSerializer,
)


class RoadGridViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = RoadGrid.objects.all()
    serializer_class = RoadGridSerializer


class ContractorViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = Contractor.objects.all()
    serializer_class = ContractorSerializer


class CleaningContractViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = CleaningContract.objects.select_related("grid", "contractor")
    serializer_class = CleaningContractSerializer


class PhotoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """证据照片。上传即计算精确指纹与感知哈希，并生成疑似重复候选。"""

    queryset = Photo.objects.all()
    serializer_class = PhotoSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        image = data["image"]
        image_bytes = image.read()
        photo = services.ingest_photo(
            image_bytes=image_bytes,
            filename=image.name,
            taken_at=data["taken_at"],
            location=services.make_point(data["lon"], data["lat"]),
            category=data.get("category", ProblemEvent.Category.OTHER),
        )
        return Response(self.get_serializer(photo).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=LinkPhotoSerializer, responses=PhotoSerializer)
    @action(detail=True, methods=["post"])
    def link(self, request, pk=None):
        """人工把照片关联到既有事件（位置/时间合理性校验不通过返回 409）。"""
        photo = self.get_object()
        serializer = LinkPhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = get_object_or_404(ProblemEvent, pk=serializer.validated_data["event_id"])
        services.link_photo_to_event(photo, event)
        photo.refresh_from_db()
        return Response(self.get_serializer(photo).data)


class DuplicateCandidateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """疑似重复候选：仅提示，合并与否由人工按位置、时间判断。"""

    queryset = DuplicateCandidate.objects.select_related("photo_a", "photo_b")
    serializer_class = DuplicateCandidateSerializer

    @extend_schema(request=ConfirmCandidateSerializer, responses=DuplicateCandidateSerializer)
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """确认两张照片是同一问题：合并事件，多余处罚作废。"""
        serializer = ConfirmCandidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidate = services.confirm_candidate(self.get_object(), note=serializer.validated_data["note"])
        return Response(self.get_serializer(candidate).data)

    @extend_schema(request=RejectCandidateSerializer, responses=DuplicateCandidateSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """确认不是同一问题；误传场景可作废后传照片及其孤立事件。"""
        serializer = RejectCandidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidate = services.reject_candidate(
            self.get_object(),
            note=serializer.validated_data["note"],
            void_newer_photo=serializer.validated_data["void_newer_photo"],
        )
        return Response(self.get_serializer(candidate).data)


class ProblemEventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = ProblemEvent.objects.select_related("grid").prefetch_related("photos")
    serializer_class = ProblemEventSerializer

    @extend_schema(request=RectificationReportSerializer, responses=RectificationReportSerializer)
    @action(detail=True, methods=["post"], url_path="rectification-callback")
    def rectification_callback(self, request, pk=None):
        """整改回调（幂等）：相同幂等键重复上报返回原记录，不重复处理。"""
        event = self.get_object()
        serializer = RectificationReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data["event"] = event
        report, created = services.report_rectification(
            event=event,
            idempotency_key=serializer.validated_data["idempotency_key"],
            reported_at=serializer.validated_data["reported_at"],
            note=serializer.validated_data.get("note", ""),
        )
        return Response(
            RectificationReportSerializer(report).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PenaltyUnitViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """处罚单元：一笔扣分对应唯一处罚单元，版本链完整可追溯。"""

    queryset = PenaltyUnit.objects.select_related("event", "contract", "contractor").prefetch_related(
        "versions", "event__photos"
    )
    serializer_class = PenaltyUnitSerializer

    @extend_schema(request=ApprovePenaltySerializer, responses=PenaltyUnitSerializer)
    @action(detail=True, methods=["post"], url_path="approve-review")
    def approve_review(self, request, pk=None):
        """复核通过：锁定当前处罚版本，之后更正只能追加。"""
        unit = self.get_object()
        services.approve_penalty(unit)
        # 锁定的是新查出的版本实例，必须清掉 prefetch 缓存里的旧对象再序列化
        unit.refresh_from_db()
        return Response(self.get_serializer(unit).data)

    @extend_schema(request=CorrectPenaltySerializer, responses=PenaltyUnitSerializer)
    @action(detail=True, methods=["post"])
    def correct(self, request, pk=None):
        """追加更正版本（历史版本保持不变）。"""
        unit = self.get_object()
        serializer = CorrectPenaltySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.correct_penalty(unit, **serializer.validated_data)
        unit.refresh_from_db()
        return Response(self.get_serializer(unit).data, status=status.HTTP_201_CREATED)


class EscalationRunView(APIView):
    """触发一轮逾期升级（基于可注入时钟），返回新追加的处罚版本。"""

    @extend_schema(request=None, responses=EscalationResultSerializer)
    def post(self, request):
        created = services.run_escalation()
        from .serializers import PenaltyVersionSerializer

        return Response({"created_versions": PenaltyVersionSerializer(created, many=True).data})

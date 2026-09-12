from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("grids", views.RoadGridViewSet, basename="grid")
router.register("contractors", views.ContractorViewSet, basename="contractor")
router.register("contracts", views.CleaningContractViewSet, basename="contract")
router.register("photos", views.PhotoViewSet, basename="photo")
router.register("candidates", views.DuplicateCandidateViewSet, basename="candidate")
router.register("events", views.ProblemEventViewSet, basename="event")
router.register("penalties", views.PenaltyUnitViewSet, basename="penalty")

urlpatterns = [
    path("", include(router.urls)),
    path("escalations/run/", views.EscalationRunView.as_view(), name="escalation-run"),
]

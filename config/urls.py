from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

urlpatterns = [
    path("api/schema/", SpectacularAPIView.as_view(), name="openapi-schema"),
    path("api/", include("assessment.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

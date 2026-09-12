import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "drf_spectacular",
    "assessment",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.environ.get("POSTGRES_DB", "sanitation"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "54329"),
    }
}

# GeoDjango 需要显式指向解包在用户目录下的 GDAL/GEOS 动态库
_gdal = os.environ.get("GDAL_LIBRARY_PATH")
_geos = os.environ.get("GEOS_LIBRARY_PATH")
if _gdal:
    GDAL_LIBRARY_PATH = _gdal
if _geos:
    GEOS_LIBRARY_PATH = _geos

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True

STATIC_URL = "static/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))
MEDIA_URL = "/media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "街道环卫考核 API",
    "DESCRIPTION": (
        "道路网格 / 保洁合同 / 问题事件 / 照片证据 / 疑似重复候选 / 处罚单元。"
        "图片感知哈希只生成疑似重复候选，事件关联以位置、时间及人工判断为准；"
        "扣分按事件发生时的合同责任区间归属；复核通过锁定处罚版本，更正只能追加。"
    ),
    "VERSION": "1.0.0",
}

# 业务参数：人工确认“同一问题”的合理性上限
ASSESSMENT_MAX_LINK_DISTANCE_M = float(os.environ.get("ASSESSMENT_MAX_LINK_DISTANCE_M", "150"))
ASSESSMENT_MAX_LINK_TIME_GAP_HOURS = float(os.environ.get("ASSESSMENT_MAX_LINK_TIME_GAP_HOURS", "72"))
ASSESSMENT_PHASH_THRESHOLD = int(os.environ.get("ASSESSMENT_PHASH_THRESHOLD", "10"))
ASSESSMENT_DEFAULT_SLA_HOURS = int(os.environ.get("ASSESSMENT_DEFAULT_SLA_HOURS", "24"))

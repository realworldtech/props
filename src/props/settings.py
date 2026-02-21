"""Django settings for PROPS project."""

import os
from pathlib import Path

from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "SECRET_KEY", "dev-secret-key-change-in-production"
)

DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]
# Always allow localhost for internal health checks (e.g. Docker healthcheck)
for _h in ("localhost", "127.0.0.1"):
    if _h not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_h)

INSTALLED_APPS = [
    "daphne",
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "django_celery_beat",
    "django_gravatar",
    "channels",
    "accounts",
    "assets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "props.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "props.context_processors.site_settings",
                "props.context_processors.user_role",
            ],
        },
    },
]

WSGI_APPLICATION = "props.wsgi.application"
ASGI_APPLICATION = "props.asgi.application"

AUTH_USER_MODEL = "accounts.CustomUser"

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    import re

    match = re.match(
        r"postgres://(?P<user>[^:]+):(?P<password>[^@]+)@"
        r"(?P<host>[^:]+):(?P<port>\d+)/(?P<name>.+)",
        DATABASE_URL,
    )
    if match:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": match.group("name"),
                "USER": match.group("user"),
                "PASSWORD": match.group("password"),
                "HOST": match.group("host"),
                "PORT": match.group("port"),
            }
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation."
        "MinimumLengthValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation."
        "CommonPasswordValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation."
        "NumericPasswordValidator"
    },
]

LANGUAGE_CODE = "en-au"
TIME_ZONE = os.environ.get("TIME_ZONE", "Australia/Sydney")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# S3 Storage Configuration
USE_S3 = os.environ.get("USE_S3", "False").lower() in ("true", "1", "yes")

if USE_S3:
    STORAGES = {
        "default": {
            "BACKEND": "props.storage.ProxiedS3Storage",
            "OPTIONS": {
                "bucket_name": os.environ.get(
                    "AWS_STORAGE_BUCKET_NAME", "assets"
                ),
                "access_key": os.environ.get("AWS_ACCESS_KEY_ID"),
                "secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
                "endpoint_url": os.environ.get("AWS_S3_ENDPOINT_URL"),
                "region_name": os.environ.get("AWS_S3_REGION_NAME", "garage"),
                "default_acl": None,
                "querystring_auth": False,
                "file_overwrite": False,
                "location": "media",
                "custom_domain": os.environ.get("AWS_S3_CUSTOM_DOMAIN"),
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage."
            "CompressedManifestStaticFilesStorage",
        },
    }
    MEDIA_URL = "/media/"
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage."
            "CompressedManifestStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# V894: Custom rate limit view returns 429 with Retry-After header
RATELIMIT_VIEW = "props.views.ratelimited_view"

AUTHENTICATION_BACKENDS = [
    "accounts.backends.EmailOrUsernameBackend",
]

# Gravatar avatars
GRAVATAR_DEFAULT_IMAGE = "mp"
GRAVATAR_DEFAULT_SIZE = 40
GRAVATAR_DEFAULT_SECURE = True
GRAVATAR_DEFAULT_RATING = "g"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "assets:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

# CSRF/session security for production
if not DEBUG:
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS]

SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", "1209600"))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Email configuration (S2.15.2-08)
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in (
    "true",
    "1",
    "yes",
)
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "False").lower() in (
    "true",
    "1",
    "yes",
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", f"noreply@localhost")

# Use console backend in DEBUG mode if SMTP not configured
if DEBUG and not EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Site configuration
SITE_NAME = os.environ.get("SITE_NAME", "PROPS")
SITE_SHORT_NAME = os.environ.get("SITE_SHORT_NAME", "PROPS")
SITE_URL = os.environ.get("SITE_URL", "")
BARCODE_PREFIX = os.environ.get("BARCODE_PREFIX", "ASSET")
BRAND_PRIMARY_COLOR = os.environ.get("BRAND_PRIMARY_COLOR", "#4F46E5")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

# Cache configuration
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("CACHE_URL", "redis://localhost:6379/1"),
    }
}

# Celery configuration
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL", "redis://localhost:6379/0"
)
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", "redis://localhost:6379/0"
)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Django Channels — Redis channel layer (§4.10.7)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                f"redis://{os.environ.get('CHANNEL_LAYERS_HOST', 'redis')}:"
                f"{os.environ.get('CHANNEL_LAYERS_PORT', '6379')}/1"
            ],
            "prefix": "asgi:",
        },
    },
}

# Print service configuration (§4.3.3.5)
PRINT_JOB_TIMEOUT_SECONDS = int(
    os.environ.get("PRINT_JOB_TIMEOUT_SECONDS", "300")
)

# V21: Require wss:// in production (default True when not DEBUG)
SECURE_WEBSOCKET = os.environ.get(
    "SECURE_WEBSOCKET", str(not DEBUG)
).lower() in ("true", "1", "yes")

# Zebra printer configuration
ZEBRA_PRINTER_HOST = os.environ.get("ZEBRA_PRINTER_HOST", "")
ZEBRA_PRINTER_PORT = int(os.environ.get("ZEBRA_PRINTER_PORT", "9100"))

# AI Image Analysis configuration
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL_NAME = os.environ.get("AI_MODEL_NAME", "claude-sonnet-4-20250514")
AI_ANALYSIS_DAILY_LIMIT = int(os.environ.get("AI_ANALYSIS_DAILY_LIMIT", "100"))
AI_MAX_IMAGE_PIXELS = int(os.environ.get("AI_MAX_IMAGE_PIXELS", "3000000"))
AI_REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", "60"))

# Brand colour palette for unfold theme
from props.colors import generate_oklch_palette

_primary_palette = generate_oklch_palette(BRAND_PRIMARY_COLOR)

# django-unfold configuration
UNFOLD = {
    "SITE_TITLE": SITE_NAME,
    "SITE_HEADER": SITE_SHORT_NAME,
    "SITE_SYMBOL": "inventory_2",
    "SITE_LOGO": "props.branding.get_site_logo",
    "SITE_FAVICONS": "props.branding.get_site_favicons",
    "COLORS": {
        "primary": _primary_palette,
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": "Assets",
                "icon": "inventory_2",
                "collapsible": True,
                "items": [
                    {
                        "title": "Assets",
                        "icon": "package_2",
                        "link": reverse_lazy("admin:assets_asset_changelist"),
                    },
                    {
                        "title": "Asset Images",
                        "icon": "image",
                        "link": reverse_lazy(
                            "admin:assets_assetimage_changelist"
                        ),
                    },
                    {
                        "title": "NFC Tags",
                        "icon": "nfc",
                        "link": reverse_lazy("admin:assets_nfctag_changelist"),
                    },
                    {
                        "title": "Transactions",
                        "icon": "swap_horiz",
                        "link": reverse_lazy(
                            "admin:assets_transaction_changelist"
                        ),
                    },
                    {
                        "title": "Stocktakes",
                        "icon": "fact_check",
                        "link": reverse_lazy(
                            "admin:assets_stocktakesession_changelist"
                        ),
                    },
                    {
                        "title": "Asset Serials",
                        "icon": "pin",
                        "link": reverse_lazy(
                            "admin:assets_assetserial_changelist"
                        ),
                    },
                    {
                        "title": "Asset Kits",
                        "icon": "backpack",
                        "link": reverse_lazy(
                            "admin:assets_assetkit_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Organisation",
                "icon": "corporate_fare",
                "collapsible": True,
                "items": [
                    {
                        "title": "Departments",
                        "icon": "business",
                        "link": reverse_lazy(
                            "admin:assets_department_changelist"
                        ),
                    },
                    {
                        "title": "Categories",
                        "icon": "category",
                        "link": reverse_lazy(
                            "admin:assets_category_changelist"
                        ),
                    },
                    {
                        "title": "Locations",
                        "icon": "location_on",
                        "link": reverse_lazy(
                            "admin:assets_location_changelist"
                        ),
                    },
                    {
                        "title": "Tags",
                        "icon": "label",
                        "link": reverse_lazy("admin:assets_tag_changelist"),
                    },
                    {
                        "title": "Hold List Statuses",
                        "icon": "playlist_add_check",
                        "link": reverse_lazy(
                            "admin:assets_holdliststatus_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Printing",
                "icon": "print",
                "collapsible": True,
                "items": [
                    {
                        "title": "Print Clients",
                        "icon": "devices",
                        "link": reverse_lazy(
                            "admin:assets_printclient_changelist"
                        ),
                    },
                    {
                        "title": "Print Requests",
                        "icon": "receipt_long",
                        "link": reverse_lazy(
                            "admin:assets_printrequest_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Users & Auth",
                "icon": "people",
                "collapsible": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy(
                            "admin:accounts_customuser_changelist"
                        ),
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
            {
                "title": "Settings",
                "icon": "settings",
                "collapsible": True,
                "items": [
                    {
                        "title": "Site Branding",
                        "icon": "palette",
                        "link": reverse_lazy(
                            "admin:assets_sitebranding_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Scheduled Tasks",
                "icon": "schedule",
                "collapsible": True,
                "items": [
                    {
                        "title": "Periodic Tasks",
                        "icon": "event_repeat",
                        "link": reverse_lazy(
                            "admin:django_celery_beat_periodictask_changelist"
                        ),
                    },
                    {
                        "title": "Intervals",
                        "icon": "timer",
                        "link": reverse_lazy(
                            "admin:django_celery_beat"
                            "_intervalschedule"
                            "_changelist"
                        ),
                    },
                    {
                        "title": "Crontabs",
                        "icon": "calendar_clock",
                        "link": reverse_lazy(
                            "admin:django_celery_beat"
                            "_crontabschedule"
                            "_changelist"
                        ),
                    },
                ],
            },
        ],
    },
}

# Logging — ensure tracebacks appear in container logs even with DEBUG=False
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# Startup validation (§S4.9.4-04)
from django.core.exceptions import ImproperlyConfigured

_missing = []

# In production, SECRET_KEY must be explicitly set
if not DEBUG and SECRET_KEY == "dev-secret-key-change-in-production":
    _missing.append("SECRET_KEY")

# In production, DATABASE_URL must be set
if not DEBUG and not DATABASE_URL:
    _missing.append("DATABASE_URL")

# ALLOWED_HOSTS must be explicitly set in production
if not DEBUG and ALLOWED_HOSTS == ["localhost", "127.0.0.1"]:
    _missing.append("ALLOWED_HOSTS")

# S3 credentials required when USE_S3 is True in production
# (In dev, credentials may be injected at runtime via garage-init)
if USE_S3 and not DEBUG:
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        _missing.append("AWS_ACCESS_KEY_ID")
    if not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        _missing.append("AWS_SECRET_ACCESS_KEY")
    if not os.environ.get("AWS_STORAGE_BUCKET_NAME"):
        _missing.append("AWS_STORAGE_BUCKET_NAME")

if _missing:
    raise ImproperlyConfigured(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        f"See .env.example for all required variables."
    )

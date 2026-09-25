"""
Keeps outdated app builds from booking while transport is priced in the app.

Builds released before in-app transport pricing (build 9 and earlier) tell the
customer on the laundry page that pickup and delivery are "not charged in the
app", then show the fee at checkout. While pricing is ON those builds must not
start or place a booking; they are told to update instead. Everything else
(login, orders, tracking, delivery confirmation, disputes, support) keeps
working.

Every Simame build sends, on every request:

    X-Client-Platform: android | ios
    X-Client-Version:  <version>+<build>      e.g. "1.0.0+9"

Compatibility is decided by the numeric build (Android versionCode, iOS build
number), never by the human version string.

Policy for requests that do not identify cleanly:

* No platform header and no version header: not a Simame mobile build (web
  dashboard, server-to-server). Allowed; those clients do not carry the
  outdated screens.
* Mobile platform with a missing or unparseable build: blocked. Every Simame
  build sends a parseable build, so this is a build that cannot be proven
  compatible.
* A version header without a platform: blocked, for the same reason.
* An unknown platform value (for example "web"): allowed, as a non-mobile client.

The minimum builds live on the admin's LogisticsPricingConfig, so raising them
needs no deploy.
"""

import re
from typing import Optional

from rest_framework import status
from rest_framework.response import Response

from logistics.models import LogisticsPricingConfig

APP_UPDATE_REQUIRED = 'APP_UPDATE_REQUIRED'
APP_UPDATE_MESSAGE = (
    "A newer version of Simame is required to continue booking. "
    "Please update the app from the Play Store."
)
APP_UPDATE_MESSAGE_IOS = (
    "A newer version of Simame is required to continue booking. "
    "Please update the app from the App Store."
)
STORE_URLS = {
    'android': 'https://play.google.com/store/apps/details?id=com.connectlaundry.app',
    'ios': 'https://apps.apple.com/app/id6806647829',
}
MOBILE_PLATFORMS = ('android', 'ios')

_BUILD_RE = re.compile(r'^\s*[^+\s]*\+(\d{1,9})\s*$')


def parse_client(request):
    """
    (platform, build) from the request headers.

    platform is 'android', 'ios', another lower-cased value, or '' when absent.
    build is an int, or None when the header is missing or malformed.
    """
    meta = getattr(request, 'META', {}) or {}
    platform = str(meta.get('HTTP_X_CLIENT_PLATFORM', '') or '').strip().lower()
    raw = str(meta.get('HTTP_X_CLIENT_VERSION', '') or '')
    match = _BUILD_RE.match(raw)
    build = int(match.group(1)) if match else None
    return platform, build, bool(raw.strip())


def update_required(request) -> Optional[Response]:
    """
    A 426 APP_UPDATE_REQUIRED response when this client may not book right
    now, else None.
    """
    config = LogisticsPricingConfig.get_active()
    if not (config and config.pricing_enabled):
        return None

    platform, build, has_version = parse_client(request)
    if platform in MOBILE_PLATFORMS:
        minimum = config.min_android_build if platform == 'android' else config.min_ios_build
        if build is not None and build >= minimum:
            return None
    elif platform == '' and has_version:
        minimum = None  # a version without a platform cannot be checked
    else:
        return None  # not a Simame mobile build

    return Response(
        {
            'status': 'error',
            'code': APP_UPDATE_REQUIRED,
            'message': APP_UPDATE_MESSAGE_IOS if platform == 'ios' else APP_UPDATE_MESSAGE,
            'data': {
                'code': APP_UPDATE_REQUIRED,
                'platform': platform or None,
                'client_build': build,
                'min_build': minimum,
                'store_url': STORE_URLS.get(platform, STORE_URLS['android']),
            },
        },
        status=status.HTTP_426_UPGRADE_REQUIRED,
    )

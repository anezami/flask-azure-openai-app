"""
Deprecated module: Custom OAuth has been removed. The app relies on Azure App Service Authentication.

This file is kept to avoid import errors in older references but should not be used.
"""

raise ImportError(
    "auth.py is deprecated. Remove imports and rely on App Service Authentication headers (X-MS-CLIENT-PRINCIPAL)."
)

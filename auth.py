import os
from functools import wraps
from flask import session, redirect, url_for, request
from authlib.integrations.flask_client import OAuth

# Global OAuth instance
oauth = OAuth()


def init_oauth(app):
    """Initialize Google OAuth using environment variables.
    Required env vars:
      - GOOGLE_CLIENT_ID
      - GOOGLE_CLIENT_SECRET
      - OAUTH_REDIRECT_URI (for production; Flask url_for fallback used in dev)
    """
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'true').lower() == 'true'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')

    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )


def require_login(f):
    """Decorator to enforce login via Google OAuth. Session-only; no persistence."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        # Allow local testing without Google auth when DISABLE_AUTH=true
        if os.getenv('DISABLE_AUTH', 'false').lower() == 'true':
            return f(*args, **kwargs)
        user = session.get('user')
        if not user:
            # Preserve original path for redirect after login
            session['post_login_redirect'] = request.path
            return redirect(url_for('login'))
        # Enforce allowlist if configured
        email = (user.get('email') or '').lower()
        if not is_email_allowed(email):
            session.clear()
            return redirect(url_for('access_denied'))
        return f(*args, **kwargs)
    return wrapper


def get_allowed_emails():
    """Return a set of lowercased emails from env ALLOWED_GOOGLE_EMAILS (comma-separated). Empty set means allow all."""
    raw = os.getenv('ALLOWED_GOOGLE_EMAILS', '').strip()
    if not raw:
        return set()
    parts = [p.strip().lower() for p in raw.split(',') if p.strip()]
    return set(parts)


def is_email_allowed(email: str) -> bool:
    allowed = get_allowed_emails()
    if not allowed:
        # No allowlist configured => allow all
        return True
    return email in allowed

import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages
from werkzeug.utils import secure_filename
from typing import Optional

# Load .env for local development only
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from auth import init_oauth, require_login, oauth, is_email_allowed
from azure_openai_client import call_chat_completion
from chunking import chunk_text_by_tokens
from langdetect import detect, DetectorFactory
from i18n import get_strings
try:
    from docx import Document  # python-docx
except Exception:
    Document = None  # type: ignore

try:
    from PyPDF2 import PdfReader
except Exception:
    PdfReader = None  # type: ignore



DetectorFactory.seed = 0  # make langdetect deterministic


def create_app():
    app = Flask(__name__)

    # Secret key for sessions (required). Use a strong random value in production.
    app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

    # Initialize Google OAuth (OIDC)
    init_oauth(app)

    # Upload configuration (text files only)
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB upload cap
    app.config['UPLOAD_EXTENSIONS'] = ['.txt', '.md', '.docx', '.pdf']
    app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Home page: single-page interface
    @app.route('/', methods=['GET'])
    @require_login
    def index():
        disable_auth = os.getenv('DISABLE_AUTH', 'false').lower() == 'true'
        user = session.get('user')
        if disable_auth and not user:
            user = {'name': 'Local User', 'email': 'local@example.com', 'picture': 'https://via.placeholder.com/32'}
        lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
        strings = get_strings(lang)
        return render_template('index.html',
                               user=user,
                               result=None,
                               source_lang=None,
                               target_lang=None,
                               mode=session.get('mode', 'grammar'),  # default to grammar
                               history=session.get('history', []),
                               disable_auth=disable_auth,
                               strings=strings,
                               ui_lang=lang)

    # Handle submission for grammar check or translation
    @app.route('/process', methods=['POST'])
    @require_login
    def process():
        return _handle_submit()

    def _handle_submit():
        text_input = request.form.get('text', '').strip()
        mode = request.form.get('mode', 'grammar')  # default to grammar
        translate_mode = mode == 'translate'
        target_language = request.form.get('target_language', '').strip() if translate_mode else ''
        session['mode'] = mode

        # Handle optional file upload
        uploaded_text = ''
        file = request.files.get('file')
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in app.config['UPLOAD_EXTENSIONS']:
                flash('Unsupported file type. Please upload .txt, .md, .docx, or .pdf.', 'error')
                return redirect(url_for('index'))
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            try:
                if ext in ('.txt', '.md'):
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        uploaded_text = f.read()
                elif ext == '.docx':
                    if Document is None:
                        flash('DOCX support not installed. Please install python-docx.', 'error')
                        return redirect(url_for('index'))
                    doc = Document(filepath)
                    uploaded_text = "\n".join(p.text for p in doc.paragraphs)
                elif ext == '.pdf':
                    if PdfReader is None:
                        flash('PDF support not installed. Please install PyPDF2.', 'error')
                        return redirect(url_for('index'))
                    reader = PdfReader(filepath)
                    pages_text = []
                    for p in reader.pages:
                        try:
                            pages_text.append(p.extract_text() or '')
                        except Exception:
                            pages_text.append('')
                    uploaded_text = "\n".join(pages_text)
            finally:
                # Clean up file immediately (no persistence)
                try:
                    os.remove(filepath)
                except Exception:
                    pass

        full_input = '\n'.join([part for part in [text_input, uploaded_text] if part])

        if not full_input:
            flash('Please provide text or upload a file.', 'error')
            return redirect(url_for('index'))

        # Auto-detect source language for display and prompt context
        try:
            source_lang = detect(full_input)
        except Exception:
            source_lang = 'unknown'

        if translate_mode and not target_language:
            flash('Please specify a target language for translation.', 'error')
            return redirect(url_for('index'))

        # System prompts per mode
        if translate_mode:
            system_prompt = (
                "You are a professional translator. Translate the user's text from the given source "
                f"language ({source_lang}) to the target language ({{target_lang}}). Preserve tone, style, and formatting. "
                "Return only the translated text without explanations."
            ).format(target_lang=target_language or 'auto')
        else:
            system_prompt = (
                "You are an expert editor. "
                "Correct grammar, spelling, punctuation, and clarity while preserving the original meaning, tone, and formatting. Do not add explanations—return only the corrected text."
                "If the language is German then apply these rules:"
                "1. Zeitform (Präteritum / Präsens): Erzählung meist im Präteritum, direkte Rede im Präsens. Wichtig: Die Regel „Erzählung in Vergangenheit, Rede in Gegenwart“ gilt fast überall, aber die konkrete Zeitform variiert je nach Sprache. "
                "2. Anführungszeichen: Deutsch: „…“ (Duden-Norm). "
                "3. Gedankenstriche: Deutsch: Halbgeviertstrich (–) mit Leerzeichen. "
                "4. Absätze / Einrückungen: Deutsch: Absätze entweder eingerückt oder mit Leerzeile abgesetzt (beides korrekt, aber konsequent anwenden). "
                "5. Lesbarkeit / Drucktauglichkeit: Einheitliche Schriftart und Größe, saubere Satzgestaltung. Zeilenabstand angepasst an Sprache (z. B. Französisch braucht oft mehr Abstand wegen längerer Wörter). Keine „Übersetzungsreste“: Der Text muss sich lesen, als wäre er ursprünglich in der Zielsprache geschrieben. Regeln für Tempus und direkte Rede sind international ähnlich, aber die konkrete Form (Präteritum vs. Simple Past vs. Passé simple) hängt von der Zielsprache ab. Typografie (Anführungszeichen, Striche, Absätze) folgt strikt den nationalen Satznormen. Drucktauglichkeit heißt: konsequent die Standards des Ziellandes anwenden, nicht die deutschen."
                )

        # Chunking configuration
        # Note: GPT-4o has a very large context window, but we keep a safe input budget.
        max_input_tokens = int(os.getenv('MAX_INPUT_TOKENS', '12000'))
        encoding_name = os.getenv('TIKTOKEN_ENCODING', 'o200k_base')

        chunks = chunk_text_by_tokens(full_input, max_tokens=max_input_tokens, encoding_name=encoding_name)

        # For each chunk, call Azure OpenAI with the selected system prompt
        responses = []
        error_message = None
        for ch in chunks:
            try:
                content = call_chat_completion(
                    system_prompt=system_prompt,
                    user_content=ch,
                    deployment_name=os.getenv('AZURE_OPENAI_DEPLOYMENT'),
                    temperature=float(os.getenv('AOAI_TEMPERATURE', '0.2')),
                    max_output_tokens=int(os.getenv('MAX_OUTPUT_TOKENS', '2048')),
                )
                responses.append(content)
            except Exception as e:
                error_message = str(e)
                break

        if error_message:
            flash(f'Error from Azure OpenAI: {error_message}', 'error')
            return redirect(url_for('index'))

        final_output = '\n'.join(responses)

        # Session-only conversation history
        history = session.get('history', [])
        history.append({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'mode': mode,
            'source_lang': source_lang,
            'target_lang': target_language if translate_mode else None,
            'input_preview': (full_input[:500] + ('…' if len(full_input) > 500 else '')),
            'output_preview': (final_output[:500] + ('…' if len(final_output) > 500 else '')),
        })
        session['history'] = history

        disable_auth = os.getenv('DISABLE_AUTH', 'false').lower() == 'true'
        user = session.get('user')
        if disable_auth and not user:
            user = {'name': 'Local User', 'email': 'local@example.com', 'picture': 'https://via.placeholder.com/32'}
        lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
        strings = get_strings(lang)
        return render_template('index.html',
                               user=user,
                               result=final_output,
                               source_lang=source_lang,
                               target_lang=target_language if translate_mode else None,
                               mode=mode,
                               history=history,
                               disable_auth=disable_auth,
                               strings=strings,
                               ui_lang=lang)

    # Auth routes
    @app.route('/login')
    def login():
        redirect_uri = os.getenv('OAUTH_REDIRECT_URI') or url_for('auth_callback', _external=True)
        if not redirect_uri:
            app.logger.error('OAUTH_REDIRECT_URI not configured and url_for failed to build absolute URL')
        return oauth.google.authorize_redirect(redirect_uri)

    @app.route('/auth/callback')
    def auth_callback():
        print(f"\n=== OAUTH CALLBACK DEBUG ===")
        print(f"Request args: {dict(request.args)}")
        print(f"Request form: {dict(request.form)}")
        print(f"Session keys: {list(session.keys())}")
        
        # Check for error parameter from Google
        if 'error' in request.args:
            error = request.args.get('error')
            error_desc = request.args.get('error_description', 'No description')
            print(f"Google OAuth error: {error} - {error_desc}")
            flash(f'Google OAuth error: {error}', 'error')
            return redirect(url_for('index'))
        
        # Check for required parameters
        if 'code' not in request.args:
            print("Missing 'code' parameter in callback")
            flash('OAuth callback missing authorization code', 'error')
            return redirect(url_for('index'))
            
        try:
            print("Attempting token exchange...")
            token = oauth.google.authorize_access_token()
            print(f"Token keys: {list(token.keys()) if token else 'None'}")
            
            print("Attempting to get userinfo from userinfo endpoint...")
            # Use the userinfo endpoint instead of parsing ID token to avoid nonce issues
            userinfo_response = oauth.google.get('https://www.googleapis.com/oauth2/v2/userinfo', token=token)
            userinfo = userinfo_response.json()
            print(f"Userinfo from endpoint: {userinfo}")
            
            if userinfo and userinfo.get('email'):
                session['user'] = {
                    'name': userinfo.get('name'),
                    'email': userinfo.get('email'),
                    'picture': userinfo.get('picture')
                }
                session['history'] = []
                
                email = userinfo.get('email', '').lower()
                print(f"Email from userinfo: {email}")
                
                if not is_email_allowed(email):
                    print(f"Email not in allowlist: {email}")
                    session.clear()
                    return redirect(url_for('access_denied'))
                    
                print("Login successful, redirecting to index")
                return redirect(url_for('index'))
            else:
                print("No email in userinfo")
                flash('Login failed: no email received', 'error')
                return redirect(url_for('index'))
                
        except Exception as e:
            print(f"Exception in OAuth callback: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            flash(f'Login failed: {str(e)}', 'error')
            return redirect(url_for('index'))

    @app.route('/logout')
    def logout():
        session.clear()
        flash('You have been logged out.', 'info')
        return redirect(url_for('index'))

    @app.route('/clear-messages')
    def clear_messages():
        # Clear all flash messages
        get_flashed_messages()
        return redirect(url_for('index'))

    @app.route('/access-denied')
    def access_denied():
        lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
        strings = get_strings(lang)
        disable_auth = os.getenv('DISABLE_AUTH', 'false').lower() == 'true'
        return render_template('access_denied.html', strings=strings, disable_auth=disable_auth), 403

    # Debug route to test OAuth configuration
    @app.route('/debug-oauth')
    def debug_oauth():
        return {
            'client_id': os.getenv('GOOGLE_CLIENT_ID', 'NOT_SET')[:20] + '...',
            'redirect_uri': os.getenv('OAUTH_REDIRECT_URI', 'NOT_SET'),
            'disable_auth': os.getenv('DISABLE_AUTH', 'NOT_SET'),
            'session_keys': list(session.keys())
        }

    # Health probe (no auth) for quick checks and Azure health probes
    @app.get('/health')
    def health():
        return {"status": "ok"}, 200

    # UI language switcher
    @app.post('/set-lang')
    def set_lang():
        lang = request.form.get('lang', 'en').lower()
        if lang not in ('en', 'de'):
            lang = 'en'
        session['ui_lang'] = lang
        return redirect(url_for('index'))

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8000')), debug=True)

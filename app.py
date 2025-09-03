import os
import base64
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages
from werkzeug.utils import secure_filename
from typing import Optional, Dict, Any

# Load .env for local development only
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

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

    # Upload configuration (text files only)
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB upload cap
    app.config['UPLOAD_EXTENSIONS'] = ['.txt', '.md', '.docx', '.pdf']
    app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    def get_authenticated_user() -> Optional[Dict[str, Any]]:
        principal_b64 = request.headers.get('X-MS-CLIENT-PRINCIPAL')
        if principal_b64:
            try:
                data = json.loads(base64.b64decode(principal_b64))
                claims = {c.get('typ'): c.get('val') for c in data.get('claims', []) if isinstance(c, dict)}
                name = claims.get('name') or claims.get('http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name')
                email = claims.get('emails') or claims.get('http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress')
                user_id = claims.get('http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier')
                return {
                    'name': name or email or 'Authenticated User',
                    'email': email or claims.get('preferred_username'),
                    'id': user_id,
                    'picture': None,
                }
            except Exception:
                pass
        name = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
        if name:
            return {'name': name, 'email': name, 'id': None, 'picture': None}
        return None

    # Home page: single-page interface
    @app.route('/', methods=['GET'])
    def index():
        user = get_authenticated_user()
        lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
        strings = get_strings(lang)
        return render_template('index.html',
                               user=user,
                               result=None,
                               source_lang=None,
                               target_lang=None,
                               mode=session.get('mode', 'grammar'),  # default to grammar
                               history=session.get('history', []),
                               strings=strings,
                               ui_lang=lang)

    # Handle submission for grammar check or translation
    @app.route('/process', methods=['POST'])
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

        user = get_authenticated_user()
        lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
        strings = get_strings(lang)
        return render_template('index.html',
                               user=user,
                               result=final_output,
                               source_lang=source_lang,
                               target_lang=target_language if translate_mode else None,
                               mode=mode,
                               history=history,
                               strings=strings,
                               ui_lang=lang)

    @app.route('/clear-messages')
    def clear_messages():
        # Clear all flash messages
        get_flashed_messages()
        return redirect(url_for('index'))

    # No custom auth-related routes; rely on App Service Authentication

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

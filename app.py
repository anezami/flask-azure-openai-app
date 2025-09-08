import os
import base64
import json
import re
import time
import random
import logging
import threading
from queue import Queue
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages, jsonify, Response, stream_with_context
from werkzeug.utils import secure_filename
from typing import Optional, Dict, Any, List, Tuple, Callable

# Load .env for local development only
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import azure_openai_client
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

# Basic logging setup (idempotent)
logger = logging.getLogger("text_assistant")
if not logger.handlers:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))

# Optional Application Insights / OpenTelemetry
APPINSIGHTS_CONNECTION_STRING = os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')
if APPINSIGHTS_CONNECTION_STRING:
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # type: ignore
        configure_azure_monitor(connection_string=APPINSIGHTS_CONNECTION_STRING)
        logger.info("Application Insights telemetry configured")
    except Exception as e:
        logger.warning(f"Failed to configure Application Insights: {e}")

METRICS_FILE_PATH = os.getenv('METRICS_FILE_PATH', os.path.join(os.getcwd(), 'metrics.log'))
METRICS_FILE_LOCK = threading.Lock()

def _persist_metric(record: dict):
    """Append a JSON line to metrics log file (best-effort)."""
    try:
        line = json.dumps(record, ensure_ascii=False)
        with METRICS_FILE_LOCK:
            with open(METRICS_FILE_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
    except Exception:
        logger.debug("Failed to persist metric", exc_info=True)

def _log_json(event: str, **fields: Any) -> None:
    try:
        payload = {"event": event, **fields}
        logger.info(json.dumps(payload))
    except Exception:
        logger.debug("Failed to log json payload", exc_info=True)

def should_retry(exc: Exception, retryable_status_codes: List[int]) -> bool:
    """Return True if this exception is retryable based on status codes or type.

    We inspect common attributes (status_code, status) and message text for 429/rate limiting.
    """
    status = None
    for attr in ("status_code", "status", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            status = val
            break
    if status is not None:
        if status in retryable_status_codes:
            return True
        if status >= 500:
            return True
    # Text heuristics (fallback)
    msg = str(exc).lower()
    if any(tok in msg for tok in ["rate limit", "too many requests", "retry later"]):
        return True
    return False


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

    def is_email_allowed(user: Optional[Dict[str, Any]]) -> bool:
        """
        Enforce an application-level email allowlist. The list is provided via
        the ALLOWED_EMAILS app setting (comma-separated). If ALLOWED_EMAILS is empty,
        all authenticated users are allowed. When DISABLE_AUTH=true, always allow.
        """
        if os.getenv('DISABLE_AUTH', 'false').lower() == 'true':
            return True
        allowed = [e.strip().lower() for e in os.getenv('ALLOWED_EMAILS', '').split(',') if e.strip()]
        if not allowed:
            return True  # no restriction
        if not user:
            return False
        email = (user.get('email') or '').lower()
        return email in allowed

    # Home page: single-page interface
    @app.route('/', methods=['GET'])
    def index():
        user = get_authenticated_user()
        if not is_email_allowed(user):
            # If running locally without Easy Auth, permit bypass with DISABLE_AUTH=true
            return render_template('access_denied.html', disable_auth=(os.getenv('DISABLE_AUTH', 'false').lower() == 'true')), 403
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
    # In-memory store for background jobs (simple, per-process only)
    jobs: Dict[str, Dict[str, Any]] = {}
    jobs_lock = threading.Lock()

    def _create_job_id() -> str:
        return base64.urlsafe_b64encode(os.urandom(9)).decode('utf-8').rstrip('=')

    @app.route('/process', methods=['POST'])
    def process():
        # Start async job then redirect to index which will poll
        return _handle_submit(async_mode=True)

    @app.get('/job/<job_id>/status')
    def job_status(job_id: str):
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'not_found'}), 404
        # Exclude non-serializable / internal fields
        return jsonify({k: v for k, v in job.items() if k not in ('raw_chunks', 'queue')})

    @app.get('/job/<job_id>/stream')
    def job_stream(job_id: str):
        """Server-Sent Events stream for job progress."""
        def event_gen():
            while True:
                with jobs_lock:
                    job = jobs.get(job_id)
                if not job:
                    yield 'event: error\ndata: {"error":"not_found"}\n\n'
                    return
                q: Queue = job.get('queue')  # type: ignore
                if q is None:
                    yield 'event: error\ndata: {"error":"queue_missing"}\n\n'
                    return
                try:
                    item = q.get(timeout=5)
                except Exception:
                    # heartbeat to keep connection alive
                    yield 'event: ping\ndata: {}\n\n'
                    continue
                if item:
                    yield f"data: {json.dumps(item)}\n\n"
                    if item.get('type') == 'final':
                        return
        return Response(stream_with_context(event_gen()), mimetype='text/event-stream')

    def _handle_submit(async_mode: bool = False):
        user = get_authenticated_user()
        if not is_email_allowed(user):
            return render_template('access_denied.html', disable_auth=(os.getenv('DISABLE_AUTH', 'false').lower() == 'true')), 403
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
            # Strong instruction: only raw translated text, no labels, no commentary
            system_prompt = (
                "You are a professional translator. Translate the user's text from the detected source "
                f"language ({source_lang}) to the target language ({{target_lang}}). Preserve tone, style, meaning, register, and line breaks/formatting. "
                "Return ONLY the translated text itself. Do NOT prepend labels, explanations, apologies, summaries, code fences, markdown headers, quotes, or phrases like 'Translation:', 'Here is the translation', or similar. Output strictly the final translated text."
            ).format(target_lang=target_language or 'auto')
        else:
            system_prompt = (
                "You are an expert copy editor. Improve grammar, spelling, punctuation, clarity, and style while preserving meaning, tone, formatting, and line breaks. "
                "Return ONLY the corrected text itself with no added labels, no introductory phrases, no explanations, no code fences, and no quotes. Do NOT output phrases like 'Corrected text:', 'Here is', or similar. "
                "If the language is German then apply these rules: "
                "1. Zeitform (Präteritum / Präsens): Erzählung meist im Präteritum, direkte Rede im Präsens. "
                "2. Anführungszeichen: Deutsch: „…“ (Duden-Norm). "
                "3. Gedankenstriche: Deutsch: Halbgeviertstrich (–) mit Leerzeichen. "
                "4. Absätze / Einrückungen: Einheitlich (Einrückung oder Leerzeile). "
                "5. Lesbarkeit: Einheitliche Typografie, keine Übersetzungsreste. Typografieregeln folgen der Zielsprache. "
                "Output ONLY the fully corrected text, nothing else."
            )

    # Chunking configuration
        # Note: GPT-4o has a very large context window, but we keep a safe input budget.
        max_input_tokens = int(os.getenv('MAX_INPUT_TOKENS', '12000'))
        encoding_name = os.getenv('TIKTOKEN_ENCODING', 'o200k_base')

        chunks = chunk_text_by_tokens(full_input, max_tokens=max_input_tokens, encoding_name=encoding_name)

        # For each chunk, call Azure OpenAI with the selected system prompt
        responses: List[Optional[str]] = [None] * len(chunks)
        error_message: Optional[str] = None
        chunk_metrics: List[Dict[str, Any]] = []  # one per chunk
        job_id: Optional[str] = None
        if async_mode:
            job_id = _create_job_id()
            with jobs_lock:
                jobs[job_id] = {
                    'id': job_id,
                    'status': 'pending',
                    'created_utc': datetime.utcnow().isoformat() + 'Z',
                    'mode': mode,
                    'chunks_total': len(chunks),
                    'chunks_completed': 0,
                    'chunks_failed': 0,
                    'progress_percent': 0.0,
                    'result': None,
                    'error': None,
                    'metrics': [],
                    'queue': Queue(maxsize=100),  # SSE event queue
                }

        # Parallelize chunk processing (best-effort) while preserving sequence.
        max_parallel = max(1, int(os.getenv('MAX_PARALLEL_REQUESTS', '4')))
        temperature = float(os.getenv('AOAI_TEMPERATURE', '0.2'))
        max_output_tokens = int(os.getenv('MAX_OUTPUT_TOKENS', '2048'))
        deployment = os.getenv('AZURE_OPENAI_DEPLOYMENT')

        # Retry configuration
        retry_max_attempts = max(1, int(os.getenv('RETRY_MAX_ATTEMPTS', '3')))
        retry_base_delay = float(os.getenv('RETRY_BASE_DELAY_SECS', '1.0'))
        retry_backoff = float(os.getenv('RETRY_BACKOFF_FACTOR', '2.0'))
        retry_jitter = float(os.getenv('RETRY_JITTER_SECS', '0.25'))
        retryable_codes_env = os.getenv('RETRY_STATUS_CODES', '429,500,502,503,504')
        retryable_status_codes = [int(c.strip()) for c in retryable_codes_env.split(',') if c.strip().isdigit()]
        circuit_breaker_threshold = int(os.getenv('CIRCUIT_BREAKER_FAILURE_THRESHOLD', '3'))
        consecutive_failures = 0

        start_time = time.time()

        def process_chunk_with_retry(idx: int, ch_text: str) -> Tuple[int, str, Dict[str, Any]]:
            attempt = 0
            start_chunk = time.time()
            last_error: Optional[str] = None
            while True:
                attempt += 1
                try:
                    t0 = time.time()
                    content_local = azure_openai_client.call_chat_completion(
                        system_prompt=system_prompt,
                        user_content=ch_text,
                        deployment_name=deployment,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    )
                    duration_call = time.time() - t0
                    cleaned = sanitize_model_output(content_local)
                    metric = {
                        'chunk_index': idx,
                        'attempts': attempt,
                        'status': 'success',
                        'call_duration_secs': round(duration_call, 3),
                        'total_chunk_duration_secs': round(time.time() - start_chunk, 3),
                        'error': None,
                    }
                    _log_json('chunk_processed', **metric)
                    metric_line = {
                        'event': 'chunk_processed',
                        'mode': mode,
                        'job_id': job_id,
                        **metric,
                    }
                    _persist_metric(metric_line)
                    return idx, cleaned, metric
                except Exception as exc:  # Controlled retry logic
                    last_error = str(exc)
                    retryable = should_retry(exc, retryable_status_codes)
                    if not retryable or attempt >= retry_max_attempts:
                        metric = {
                            'chunk_index': idx,
                            'attempts': attempt,
                            'status': 'failed',
                            'error': last_error,
                            'retryable': retryable,
                            'total_chunk_duration_secs': round(time.time() - start_chunk, 3),
                        }
                        _log_json('chunk_failed', **metric)
                        _persist_metric({'event': 'chunk_failed', 'mode': mode, 'job_id': job_id, **metric})
                        raise
                    delay = retry_base_delay * (retry_backoff ** (attempt - 1))
                    if retry_jitter > 0:
                        delay += random.uniform(0, retry_jitter)
                    _log_json('chunk_retrying', chunk_index=idx, attempt=attempt, next_delay_secs=round(delay, 3), error=str(exc), retryable=retryable)
                    _persist_metric({'event': 'chunk_retrying', 'mode': mode, 'job_id': job_id, 'chunk_index': idx, 'attempt': attempt, 'delay': round(delay,3), 'error': str(exc), 'retryable': retryable})
                    time.sleep(delay)

        def _execute_job(job_id_local: Optional[str]=None):
            nonlocal error_message, consecutive_failures
            if len(chunks) == 1 or max_parallel == 1:
                try:
                    _, cleaned_single, metric = process_chunk_with_retry(0, chunks[0])
                    responses[0] = cleaned_single
                    chunk_metrics.append(metric)
                except Exception as e:
                    error_message = str(e)
                    # Record failure metric so final accounting reflects failure
                    chunk_metrics.append({
                        'chunk_index': 0,
                        'attempts': metric['attempts'] if 'metric' in locals() else 1,
                        'status': 'failed',
                        'error': str(e),
                    })
            else:
                indexed_chunks = list(enumerate(chunks))
                with ThreadPoolExecutor(max_workers=min(max_parallel, len(chunks))) as executor:
                    future_map = {executor.submit(process_chunk_with_retry, idx, ch_text): idx for idx, ch_text in indexed_chunks}
                    for future in as_completed(future_map):
                        if error_message:
                            break
                        try:
                            idx, cleaned, metric = future.result()
                            responses[idx] = cleaned
                            chunk_metrics.append(metric)
                            consecutive_failures = 0
                            if async_mode and job_id_local:
                                with jobs_lock:
                                    job = jobs.get(job_id_local)
                                    if job:
                                        job['chunks_completed'] = sum(1 for r in responses if r)
                                        total = job.get('chunks_total', 0) or 1
                                        job['progress_percent'] = round(100.0 * job['chunks_completed'] / total, 1)
                                        q: Queue = job.get('queue')  # type: ignore
                                        if q:
                                            try:
                                                q.put_nowait({'type': 'progress', 'chunks_completed': job['chunks_completed'], 'chunks_total': total, 'progress_percent': job['progress_percent']})
                                            except Exception:
                                                pass
                        except Exception as e:  # Capture first error; allow graceful handling
                            consecutive_failures += 1
                            chunk_index = future_map[future]
                            error_message = f"Chunk {chunk_index} failed: {e}" if not error_message else error_message
                            # Record failure metric for final accounting
                            chunk_metrics.append({
                                'chunk_index': chunk_index,
                                'attempts': 1,
                                'status': 'failed',
                                'error': str(e),
                            })
                            _log_json('chunk_error', chunk_index=future_map[future], error=str(e), consecutive_failures=consecutive_failures)
                            _persist_metric({'event': 'chunk_error', 'mode': mode, 'job_id': job_id_local, 'chunk_index': future_map[future], 'error': str(e), 'consecutive_failures': consecutive_failures})
                            if async_mode and job_id_local:
                                with jobs_lock:
                                    job = jobs.get(job_id_local)
                                    if job:
                                        job['chunks_failed'] = job.get('chunks_failed',0) + 1
                                        total = job.get('chunks_total', 0) or 1
                                        done = job.get('chunks_completed',0)
                                        job['progress_percent'] = round(100.0 * done / total, 1)
                                        q: Queue = job.get('queue')  # type: ignore
                                        if q:
                                            try:
                                                q.put_nowait({'type': 'error', 'message': str(e), 'chunks_failed': job['chunks_failed']})
                                            except Exception:
                                                pass
                            if consecutive_failures >= circuit_breaker_threshold:
                                error_message = f"Processing aborted after {consecutive_failures} consecutive chunk failures (circuit breaker tripped). Last error: {e}"
                                _log_json('circuit_breaker_open', failures=consecutive_failures, threshold=circuit_breaker_threshold)
                                _persist_metric({'event': 'circuit_breaker_open', 'mode': mode, 'job_id': job_id_local, 'failures': consecutive_failures, 'threshold': circuit_breaker_threshold})
                                break
                            break
                if error_message:
                    responses.clear()

            # Finalize job record if async
            if async_mode and job_id_local:
                with jobs_lock:
                    job = jobs.get(job_id_local)
                    if job:
                        job['chunks_completed'] = sum(1 for r in responses if r)
                        computed_failed = sum(1 for m in chunk_metrics if m.get('status')=='failed')
                        # Preserve higher of real-time increments vs computed failures
                        job['chunks_failed'] = max(job.get('chunks_failed', 0), computed_failed)
                        if error_message:
                            job['status'] = 'failed'
                            job['error'] = error_message
                        else:
                            job['status'] = 'succeeded'
                            job['result'] = '\n'.join(r for r in responses if r)
                        total = job.get('chunks_total', 0) or 1
                        job['progress_percent'] = round(100.0 * job['chunks_completed'] / total, 1)
                        job['metrics'] = chunk_metrics
                        q: Queue = job.get('queue')  # type: ignore
                        if q:
                            try:
                                q.put_nowait({'type': 'final', 'status': job['status'], 'error': job.get('error'), 'progress_percent': job['progress_percent']})
                            except Exception:
                                pass
                total_duration_local = time.time() - start_time
                _persist_metric({'event': 'job_finished', 'mode': mode, 'job_id': job_id_local, 'status': 'failed' if error_message else 'succeeded', 'duration_secs': round(total_duration_local,3)})

        if async_mode:
            # Launch background thread
            def _run():
                with jobs_lock:
                    if job_id and job_id in jobs:
                        jobs[job_id]['status'] = 'running'
                        q: Queue = jobs[job_id].get('queue')  # type: ignore
                        if q:
                            try:
                                q.put_nowait({'type': 'started'})
                            except Exception:
                                pass
                _persist_metric({'event': 'job_started', 'mode': mode, 'job_id': job_id, 'chunks': len(chunks)})
                try:
                    _execute_job(job_id)
                except Exception as e:
                    _persist_metric({'event': 'job_thread_exception', 'mode': mode, 'job_id': job_id, 'error': str(e)})
            threading.Thread(target=_run, daemon=True).start()
            # Render page with job id placeholder; front-end will poll
            lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
            strings = get_strings(lang)
            history = session.get('history', [])
            return render_template('index.html',
                                   user=user,
                                   result=None,
                                   source_lang=source_lang,
                                   target_lang=target_language if translate_mode else None,
                                   mode=mode,
                                   history=history,
                                   strings=strings,
                                   ui_lang=lang,
                                   job_id=job_id)

        # Synchronous path below
        _execute_job(None)

        if error_message:
            # Optional: provide partial output preview (only successful leading contiguous chunks)
            partial_chunks: List[str] = []
            for r in responses:
                if r is None:
                    break
                partial_chunks.append(r)
            partial_preview = '\n'.join(partial_chunks)[:500]
            flash_msg = f"Error processing text: {error_message}."
            if partial_chunks:
                flash_msg += " Partial output generated up to the point of failure (discarded)."
            flash(flash_msg, 'error')
            return redirect(url_for('index'))

        final_output = '\n'.join(r for r in responses if r)
        total_duration = time.time() - start_time

        # Session-only conversation history
        history = session.get('history', [])
        # Summaries
        success_count = sum(1 for m in chunk_metrics if m.get('status') == 'success')
        failed_count = sum(1 for m in chunk_metrics if m.get('status') == 'failed')
        retried_chunks = sum(1 for m in chunk_metrics if m.get('attempts', 1) > 1)

        history.append({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'mode': mode,
            'source_lang': source_lang,
            'target_lang': target_language if translate_mode else None,
            'input_preview': (full_input[:500] + ('…' if len(full_input) > 500 else '')),
            'output_preview': (final_output[:500] + ('…' if len(final_output) > 500 else '')),
            'chunks': len(chunks),
            'duration_seconds': round(total_duration, 3),
            'chunks_success': success_count,
            'chunks_failed': failed_count,
            'chunks_retried': retried_chunks,
        })
        session['history'] = history

        # user already resolved above for allowlist
        lang = session.get('ui_lang', os.getenv('UI_LANG', 'en'))
        strings = get_strings(lang)
        _persist_metric({'event': 'job_finished_sync', 'mode': mode, 'chunks': len(chunks), 'status': 'succeeded', 'duration_secs': round(total_duration,3)})
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


def sanitize_model_output(text: str) -> str:
    """Remove common leading boilerplate or labels the model might still emit.

    Steps:
    - Strip surrounding whitespace
    - Remove enclosing triple backticks (with optional language tag)
    - Remove a small set of leading label lines (e.g., 'Translation:', 'Corrected text:', etc.)
    - Remove leading phrases like 'Here is the translation:' / 'Here is the corrected text:'
    - Return the cleaned string stripped of extra blank lines at start/end
    """
    if not text:
        return ""
    original = text
    t = text.strip()

    # Remove surrounding ``` blocks if they wrap the entire content
    fenced_pattern = re.compile(r'^```[a-zA-Z0-9_-]*\n([\s\S]*?)\n```$', re.MULTILINE)
    m = fenced_pattern.match(t)
    if m:
        t = m.group(1).strip()

    # Remove leading phrases like 'Here is the translation:' etc. (case-insensitive)
    lead_phrase_pattern = re.compile(r'^(here (is|are) (the )?(translation|corrected text|correction)\s*:?)\s*', re.IGNORECASE)
    t = lead_phrase_pattern.sub('', t)

    # Remove single leading label lines e.g. 'Translation:' or 'Corrected text:'
    label_pattern = re.compile(r'^(translation|translated text|corrected text|correction)\s*:?\s*\n+', re.IGNORECASE)
    t = label_pattern.sub('', t)

    # If after cleaning first line is still just the label (without newline), strip it
    one_line_label = re.compile(r'^(translation|translated text|corrected text|correction)\s*:?\s*$', re.IGNORECASE)
    if one_line_label.match(t):
        t = ''

    # Collapse excessive leading blank lines
    t = re.sub(r'^(\s*\n){1,}', '', t)

    # Final trim
    t = t.strip('\n')
    return t if t else original.strip()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8000')), debug=True)

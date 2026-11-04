"""Small dependency-free client for the Cyber invite-only inference pilot."""
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

__version__ = '0.1.0'
DEFAULT_BASE_URL = 'https://f16.live/cyber/api/inference'

class CyberError(Exception):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(code)

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CyberError('redirect_refused', code)

def read_token(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    if Path(path).is_symlink():
        raise CyberError('token_file_must_not_be_symlink')
    try:
        with os.fdopen(os.open(path, flags), 'r') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise CyberError('token_file_must_be_regular')
            if os.name == 'posix' and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid()):
                raise CyberError('token_file_must_be_owned_and_private')
            token = source.read(257).strip()
    except (OSError, UnicodeError):
        raise CyberError('cannot_read_private_token_file') from None
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise CyberError('invalid_token_format')
    return token

class Client:
    def __init__(self, base_url=DEFAULT_BASE_URL, token=None, timeout=15):
        try:
            parsed = urlsplit(base_url)
            valid = parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1'))
            if not valid or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError()
            parsed.port
        except (ValueError, TypeError):
            raise CyberError('https_required_except_loopback') from None
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 60:
            raise CyberError('invalid_http_timeout')
        if token is not None and (not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token)):
            raise CyberError('invalid_token_format')
        self.base_url, self._token, self.timeout = base_url.rstrip('/'), token, timeout
        self._http = build_opener(ProxyHandler({}), _NoRedirect())

    def _call(self, route, body=None):
        headers = {'Accept': 'application/json'}
        if body is not None:
            if self._token is None:
                raise CyberError('invitation_token_required')
            headers.update({'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._token})
        request = Request(self.base_url + '/' + route,
                          data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            try:
                response = self._http.open(request, timeout=self.timeout)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise CyberError('response_size_limit', response.status)
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise CyberError('invalid_json_response', response.status) from None
                if not isinstance(value, dict):
                    raise CyberError('response_object_required', response.status)
                if response.status not in (200, 202):
                    code = value.get('error', 'http_error')
                    if not isinstance(code, str) or not re.fullmatch(r'[a-z0-9_]{1,100}', code):
                        code = 'http_error'
                    raise CyberError(code, response.status)
                return value
        except (URLError, OSError, TimeoutError):
            raise CyberError('connection_failed_reuse_request_id') from None

    def models(self):
        """Discover models and limits without sending a credential."""
        return self._call('models')

    def quote(self, *, request_id, model, prompt, max_output_tokens=128):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise CyberError('request_id_required')
        if model not in ('small', 'large'):
            raise CyberError('unknown_model')
        if not isinstance(prompt, str) or not 1 <= len(prompt.encode()) <= 2048:
            raise CyberError('prompt_size_limit')
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 512:
            raise CyberError('output_token_limit')
        return self._call('quotes', dict(request_id=request_id, model=model, prompt=prompt,
                                         max_output_tokens=max_output_tokens))

    def execute(self, quote_id):
        return self._call('execute', {'quote_id': quote_id})

    def status(self, quote_id):
        return self._call('status', {'quote_id': quote_id})

    def run(self, *, request_id, model, prompt, max_output_tokens=128, poll_timeout=900, poll_interval=1):
        if (any(type(v) not in (int, float) for v in (poll_timeout, poll_interval))
                or not 0 < poll_timeout <= 3600 or not .1 <= poll_interval <= 30):
            raise CyberError('invalid_poll_limits')
        quote = self.quote(request_id=request_id, model=model, prompt=prompt, max_output_tokens=max_output_tokens)
        qid = quote.get('quote_id')
        if not isinstance(qid, str) or not qid:
            raise CyberError('invalid_quote_response')
        state = quote.get('state')
        if state == 'quoted':
            self.execute(qid)
        elif state not in ('running', 'completed'):
            raise CyberError('attempt_requires_operator_review')
        deadline = time.monotonic() + poll_timeout
        while True:
            result = self.status(qid)
            if result.get('state') == 'completed':
                return result
            if result.get('state') != 'running':
                raise CyberError('attempt_requires_operator_review')
            if time.monotonic() >= deadline:
                raise CyberError('poll_timeout_reuse_request_id')
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

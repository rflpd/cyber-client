"""Experimental client for a future invited house API; no default endpoint.

Each method makes at most one HTTP request. This is not a durable orchestrator:
callers retain request/quote/attempt intent and reconcile uncertain work on restart.
No code execution, paid fallback, qualification or payment functionality exists.
"""
import copy
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import ssl
import stat
import time
import threading
from urllib.parse import urlsplit

from . import CyberError
from . import house_contract as contract

MAX_RESPONSE = 262144
MAX_REQUEST = 65536


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def _decode(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError()
            value[key] = item
        return value
    def reject(_):
        raise ValueError()
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)


def _endpoint(value):
    try:
        if (not isinstance(value, str) or len(value) > 2048
                or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
                or any(c in value for c in ('?', '#', '\\', '%'))):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname
                or '@' in parsed.netloc or parsed.username is not None or parsed.password is not None
                or not parsed.path.rstrip('/').endswith('/v1/house')
                or any(part in ('.', '..') for part in parsed.path.split('/'))
                or '//' in parsed.path or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
        if parsed.scheme == 'http' and not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise CyberError('invalid_house_endpoint') from None


def _token(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) not in (0o400, 0o600)
                    or not 32 <= before.st_size <= 258):
                raise ValueError()
            raw = stream.read(259)
            after = os.fstat(stream.fileno())
            fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
                      'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if any(getattr(before, k) != getattr(after, k) for k in fields):
                raise ValueError()
        token = raw.decode('ascii').rstrip('\r\n')
        if not re.fullmatch('[A-Za-z0-9_-]{32,256}', token):
            raise ValueError()
        return token
    except (OSError, ValueError, UnicodeError, TypeError):
        raise CyberError('private_house_token_required') from None


class HouseJob:
    """Caller-owned snapshot; generation completion never means semantic acceptance."""
    def __init__(self, owner, request, quote):
        self._owner = owner
        self._request_bytes = _encode(request)
        self._quote_bytes = _encode(quote)
        self._state = quote['state']

    @property
    def request(self):
        return _decode(self._request_bytes)

    @property
    def quote(self):
        return _decode(self._quote_bytes)

    @property
    def quote_id(self):
        return self.quote['quote_id']


class HouseClient:
    """Explicit-endpoint, one-request operations for the unserved house protocol.

    The operator must provide a reviewed deployed endpoint and permitted access.
    Instantiating this object neither discovers nor activates a house service.
    """
    def __init__(self, *, base_url, token_file, timeout=15):
        self._endpoint = _endpoint(base_url)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not .1 <= timeout <= 60):
            raise CyberError('invalid_http_timeout')
        self._token_file = token_file
        self.timeout = timeout
        self._submitted = set()
        self._submission_lock = threading.Lock()

    def _call(self, operation, body):
        if operation not in ('quotes', 'execute', 'status'):
            raise CyberError('invalid_house_operation')
        try:
            payload = _encode(body)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise CyberError('invalid_house_request') from None
        if len(payload) > MAX_REQUEST:
            raise CyberError('request_size_limit')
        token = _token(self._token_file)
        p = self._endpoint
        connection = (http.client.HTTPSConnection(p.hostname, p.port, timeout=self.timeout,
            context=ssl.create_default_context()) if p.scheme == 'https' else
            http.client.HTTPConnection(p.hostname, p.port, timeout=self.timeout))
        started = time.monotonic()
        try:
            connection.connect()
            sock = connection.sock
            remaining = self.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise CyberError('house_transport_outcome_unknown')
            sock.settimeout(remaining)
            connection.request('POST', p.path.rstrip('/') + '/' + operation, body=payload,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                    'Accept-Encoding': 'identity', 'Connection': 'close', 'Authorization': 'Bearer ' + token})
            response = connection.getresponse()
            with response:
                if 300 <= response.status < 400:
                    raise CyberError('redirect_refused', response.status)
                if response.status not in (200, 202):
                    code = {401: 'unauthorized', 403: 'unauthorized', 409: 'house_review_required',
                            429: 'quota_exhausted'}.get(response.status, 'house_http_error')
                    raise CyberError(code, response.status)
                if (response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json'
                        or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
                    raise CyberError('invalid_house_response')
                length = response.getheader('Content-Length')
                transfer = response.getheader('Transfer-Encoding')
                if transfer is not None and (transfer.lower() != 'chunked' or length is not None):
                    raise CyberError('invalid_house_response')
                if length is not None and (not length.isdecimal() or len(length) > 10 or int(length) > MAX_RESPONSE):
                    raise CyberError('response_size_limit')
                raw = bytearray()
                while True:
                    remaining = self.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise CyberError('house_transport_outcome_unknown')
                    sock.settimeout(remaining)
                    block = response.read1(min(4096, MAX_RESPONSE + 1 - len(raw)))
                    if not block:
                        break
                    raw.extend(block)
                    if len(raw) > MAX_RESPONSE:
                        raise CyberError('response_size_limit')
                    if length is not None and len(raw) == int(length):
                        break
                if length is not None and len(raw) != int(length):
                    raise CyberError('invalid_house_response')
                value = _decode(raw)
                if not isinstance(value, dict):
                    raise CyberError('invalid_house_response')
                return value
        except (OSError, TimeoutError, http.client.HTTPException):
            raise CyberError('house_transport_outcome_unknown') from None
        except (ValueError, UnicodeError, RecursionError):
            raise CyberError('invalid_house_response') from None
        finally:
            connection.close()

    def quote(self, request):
        """Request one quote for the caller's saved, exact request; never auto-execute."""
        try:
            saved = copy.deepcopy(request)
            contract.request_record(saved)
        except (contract.ProfileUnavailable, ValueError, TypeError, UnicodeError, RecursionError):
            raise CyberError('invalid_house_request') from None
        response = self._call('quotes', saved)
        try:
            quote = contract.validate_quote(saved, response)
        except (contract.HouseClientMismatch, ValueError, TypeError, KeyError, UnicodeError):
            raise CyberError('house_response_binding_mismatch') from None
        return HouseJob(self, saved, quote)

    def restore(self, request, saved_quote):
        """Restore a saved job for status reads only; never re-authorize execution.

        Persist request, quote and submission intent before execute. After an
        interruption restore those exact records and reconcile via status.
        """
        try:
            saved = copy.deepcopy(request)
            quote = contract.validate_quote(saved, {
                'api_schema': contract.API_SCHEMA, 'record': copy.deepcopy(saved_quote)})
        except (contract.HouseClientMismatch, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise CyberError('house_response_binding_mismatch') from None
        job = HouseJob(self, saved, quote)
        with self._submission_lock:
            self._submitted.add(job.quote_id)
        return job

    def _job(self, job):
        if type(job) is not HouseJob or job._owner is not self:
            raise CyberError('house_job_owner_mismatch')
        return job.quote_id

    def _observe(self, job, response):
        try:
            record = response.get('record', {})
            if 'output' in record:
                validated = contract.validate_result(job.request, job.quote, response)
                if job._state == 'interrupted':
                    raise contract.HouseClientMismatch()
                job._state = 'completed'
                return validated
            quote = contract.validate_quote(job.request, response)
            fixed = lambda q: {k: v for k, v in q.items() if k not in ('state', 'outcome')}
            if fixed(quote) != fixed(job.quote):
                raise contract.HouseClientMismatch()
            allowed = {'quoted': {'quoted', 'running', 'completed', 'interrupted'},
                       'running': {'running', 'completed', 'interrupted'},
                       'completed': {'completed'}, 'interrupted': {'interrupted'}}
            if quote['state'] not in allowed[job._state]:
                raise contract.HouseClientMismatch()
            job._state = quote['state']
            return quote
        except (contract.HouseClientMismatch, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise CyberError('house_response_binding_mismatch') from None

    def execute(self, job):
        """One submission per quote per client lifetime; persist caller intent first."""
        qid = self._job(job)
        with self._submission_lock:
            if qid in self._submitted or job._state != 'quoted':
                raise CyberError('house_review_required')
            # Consumed locally before transport, including lost acknowledgement.
            self._submitted.add(qid)
        return self._observe(job, self._call('execute', {'quote_id': qid}))

    def status(self, job):
        """One read of existing state. Never creates or executes replacement work."""
        qid = self._job(job)
        return self._observe(job, self._call('status', {'quote_id': qid}))

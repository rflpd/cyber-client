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
import socket
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


def _validate_cancellation(quote, response):
    """A scoped stop acknowledgement never proves worker teardown or acceptance."""
    try:
        record = contract._envelope(response)
        fields = {'schema', 'quote_id', 'request_sha256', 'requested', 'state_at_request',
                  'requested_at', 'last_observed_state', 'execution_prevented',
                  'cleanup_verified', 'retry_authorized', 'charged_microusd',
                  'settlement', 'semantic_acceptance'}
        if (set(record) != fields or record['schema'] != 'cyber-house-cancellation/1'
                or record['quote_id'] != quote['quote_id']
                or record['request_sha256'] != quote['request_sha256']
                or type(record['requested']) is not bool
                or type(record['execution_prevented']) is not bool
                or record['cleanup_verified'] is not False
                or record['retry_authorized'] is not False):
            raise contract.HouseClientMismatch()
        contract._zero(record)
        if record['requested']:
            state = record['state_at_request']; current = record['last_observed_state']
            if (type(record['requested_at']) is not int or record['requested_at'] <= 0
                    or (state, current, record['execution_prevented']) not in {
                        ('quoted', 'quoted', True), ('running', 'running', False),
                        ('running', 'interrupted', False)}):
                raise contract.HouseClientMismatch()
        elif (record['state_at_request'] is not None or record['requested_at'] is not None
                or record['last_observed_state'] not in ('completed', 'interrupted')
                or record['execution_prevented']):
            raise contract.HouseClientMismatch()
        return copy.deepcopy(record)
    except (contract.HouseClientMismatch, TypeError, ValueError, KeyError, AttributeError):
        raise CyberError('house_response_binding_mismatch') from None


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
        self._observation_lock = threading.RLock()
        self._observation = 'validated_quote'
        self._restored = False
        self._execution_attempted = False
        self._output_sha256 = None
        self._cancellation_observation = 'not_requested'
        self._cancellation_receipt = None

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
    def __init__(self, *, base_url, token_file, timeout=15, execution_timeout=345):
        self._endpoint = _endpoint(base_url)
        for value, maximum in ((timeout, 60), (execution_timeout, 360)):
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not .1 <= value <= maximum):
                raise CyberError('invalid_http_timeout')
        self._token_file = token_file
        self.timeout = timeout
        self.execution_timeout = execution_timeout
        self._submitted = set()
        self._submission_lock = threading.Lock()

    def _call(self, operation, body):
        if operation not in ('quotes', 'execute', 'status', 'cancel'):
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
        response_timer = None
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
            remaining = self.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise CyberError('house_transport_outcome_unknown')
            # Execute may legitimately wait for a 300s job plus cleanup. Other
            # operations keep the original short whole-request budget.
            response_budget = self.execution_timeout if operation == 'execute' else remaining
            started = time.monotonic()
            sock.settimeout(response_budget)
            def expire_response():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            response_timer = threading.Timer(response_budget, expire_response)
            response_timer.daemon = True
            try:
                response_timer.start()
            except Exception:
                raise CyberError('house_transport_outcome_unknown') from None
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
                    remaining = response_budget - (time.monotonic() - started)
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
                if time.monotonic() - started >= response_budget:
                    raise CyberError('house_transport_outcome_unknown')
                value = _decode(raw)
                if not isinstance(value, dict):
                    raise CyberError('invalid_house_response')
                return value
        except (OSError, TimeoutError, http.client.HTTPException):
            raise CyberError('house_transport_outcome_unknown') from None
        except (ValueError, UnicodeError, RecursionError):
            raise CyberError('invalid_house_response') from None
        finally:
            if response_timer is not None:
                response_timer.cancel()
                if response_timer.ident is not None:
                    response_timer.join()
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
        """Restore for status or an explicit stop request; never authorize execution.

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
        job._observation = 'restored_records'
        job._restored = True
        with self._submission_lock:
            self._submitted.add(job.quote_id)
        return job

    def _job(self, job):
        if type(job) is not HouseJob or job._owner is not self:
            raise CyberError('house_job_owner_mismatch')
        return job.quote_id

    def _observe(self, job, response):
        with job._observation_lock:
            record = self._observe_locked(job, response)
            if 'output_sha256' in record:
                if job._output_sha256 is not None and job._output_sha256 != record['output_sha256']:
                    raise CyberError('house_response_binding_mismatch')
                job._output_sha256 = record['output_sha256']
            job._observation = 'validated_response'
            return record

    def _observe_locked(self, job, response):
        try:
            record = response.get('record', {})
            cancellation = job._cancellation_receipt
            if cancellation is not None:
                if cancellation['requested']:
                    allowed = ({'quoted'} if cancellation['execution_prevented'] else
                               {'interrupted'} if cancellation['last_observed_state'] == 'interrupted'
                               else {'running', 'interrupted'})
                else:
                    allowed = {cancellation['last_observed_state']}
                if record.get('state') not in allowed or ('output' in record and cancellation['requested']):
                    raise contract.HouseClientMismatch()
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
        with self._submission_lock, job._observation_lock:
            if qid in self._submitted or job._state != 'quoted':
                raise CyberError('house_review_required')
            # Consumed locally before transport, including lost acknowledgement.
            self._submitted.add(qid)
            job._execution_attempted = True
        return self._job_call(job, 'execute', qid)

    def status(self, job):
        """One read of existing state. Never creates or executes replacement work."""
        qid = self._job(job)
        return self._job_call(job, 'status', qid)

    def cancel(self, job):
        """Request stop once, including for restored jobs. Never imply cleanup.

        Server-side cancellation is idempotent; this method makes no automatic
        retry. Consume the local execution guard before transport, even when the
        request is interrupted or its acknowledgement is lost.
        """
        qid = self._job(job)
        with self._submission_lock, job._observation_lock:
            self._submitted.add(qid)
            job._cancellation_observation = 'outcome_unknown'
        response = self._call('cancel', {'quote_id': qid})
        receipt = _validate_cancellation(job.quote, response)
        with job._observation_lock:
            allowed = {'quoted': {'quoted', 'running', 'completed', 'interrupted'},
                       'running': {'running', 'completed', 'interrupted'},
                       'completed': {'completed'}, 'interrupted': {'interrupted'}}
            if receipt['last_observed_state'] not in allowed[job._state]:
                raise CyberError('house_response_binding_mismatch')
            prior = job._cancellation_receipt
            if prior is not None:
                fixed = ('requested', 'state_at_request', 'requested_at', 'execution_prevented')
                if (any(receipt[key] != prior[key] for key in fixed)
                        or prior['last_observed_state'] in ('completed', 'interrupted')
                        and receipt['last_observed_state'] != prior['last_observed_state']):
                    raise CyberError('house_response_binding_mismatch')
            job._cancellation_receipt = copy.deepcopy(receipt)
            job._cancellation_observation = 'validated_acknowledgement'
        return receipt

    def _job_call(self, job, operation, qid):
        # Mark uncertainty before dispatch so interrupts and unexpected exits
        # cannot leave an earlier observation looking current. Re-raise all
        # failures naturally; only a fully validated response replaces this.
        with job._observation_lock:
            job._observation = ('execution_outcome_unknown' if operation == 'execute'
                                else 'status_unavailable')
        return self._observe(job, self._call(operation, {'quote_id': qid}))

    def summary(self, job):
        """One private status view for human/agent callers; no HTTP or new authority.

        The last valid server state is explicitly distinct from a failed read,
        restored records and the receipt of a complete, still-unaccepted answer.
        This projection omits the token, prompt and model output.
        """
        qid = self._job(job)
        with job._observation_lock:
            quote = job.quote
            return {
                'schema': 'cyber-house-client-status/2',
                'quote_id': qid, 'profile_id': quote['model'],
                'profile_sha256': quote['profile_sha256'],
                'task_contract_sha256': quote['task_contract_sha256'],
                'resource_contract_sha256': quote['resource_contract_sha256'],
                'last_validated_state': job._state,
                'observation': job._observation,
                'restored_without_execution_authority': job._restored,
                'execution_attempted_in_this_client': job._execution_attempted,
                'validated_output_received': job._output_sha256 is not None,
                'received_output_sha256': job._output_sha256,
                'cancellation_observation': job._cancellation_observation,
                'last_cancellation_requested': (job._cancellation_receipt['requested']
                    if job._cancellation_receipt is not None else None),
                'last_cancellation_state': (job._cancellation_receipt['last_observed_state']
                    if job._cancellation_receipt is not None else None),
                'cancellation_acknowledges_cleanup': False,
                'semantic_acceptance': False, 'independent_model_attestation': False,
                'charged_microusd': 0, 'full_cost_known': False,
                'settlement': 'disabled', 'new_execution_authorized': False,
            }

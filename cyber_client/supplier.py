"""Explicit invited buyer/supplier operations; no default deployment or retries.

The caller durably retains request IDs and intent before mutations. Server-side
accounts, qualification, permission admission, capacity and budgets remain the
authority. This client neither generates nor executes returned source.
"""
import base64
import binascii
import hashlib
import ipaddress
import math
import re
import threading
from urllib.parse import urlsplit

from . import CyberError
from ._http import _encode, _decode, post_json
from .house import _token


SCHEMA = 'cyber-supplier-api/1'
MAX_REQUEST = 65536
MAX_RESPONSE = 131072
OPERATIONS = {
    'register_service': ('suppliers/register', ('manifest', 'configuration_sha256')),
    'read_service': ('suppliers/read', ('service_id',)),
    'read_challenge': ('suppliers/challenges/read', ('challenge_id',)),
    'submit_answer': ('suppliers/answers', ('challenge_id', 'binding_sha256', 'source')),
    'declare_offer': ('suppliers/offers/declare', ('offer_id', 'challenge_id', 'price_microusd', 'slots', 'expires_at')),
    'read_offer': ('suppliers/offers/read', ('offer_id',)),
    'create_job': ('jobs/create', ('job_id', 'deadline')),
    'read_job': ('jobs/read', ('job_id',)),
    'read_payment': ('payments/read', ('job_id',)),
    'job_output': ('jobs/output', ('job_id',)),
    'job_offers': ('jobs/offers', ('job_id', 'after', 'limit')),
    'reserve_job': ('jobs/reserve', ('job_id', 'offer_id', 'scope_sha256')),
    'cancel_job': ('jobs/cancel', ('job_id',)),
    'declare_capacity': ('suppliers/capacity/declare', ('slots', 'own_reserved', 'expires_at')),
    'assigned_jobs': ('suppliers/jobs/list', ('service_id', 'after', 'limit')),
    'claim_job': ('suppliers/jobs/claim', ('job_id',)),
    'deliver_job': ('suppliers/jobs/deliver', ('job_id', 'source')),
}
ONCE = {'submit_answer': 'challenge_id', 'claim_job': 'job_id', 'deliver_job': 'job_id'}


def _endpoint(value):
    try:
        if (not isinstance(value, str) or len(value) > 2048
                or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
                or any(c in value for c in ('?', '#', '\\', '%'))):
            raise ValueError()
        p = urlsplit(value)
        if (p.scheme not in ('https', 'http') or not p.hostname or '@' in p.netloc
                or not p.path.rstrip('/').endswith('/v1')
                or any(part in ('.', '..') for part in p.path.split('/'))
                or '//' in p.path or p.port is not None and not 1 <= p.port <= 65535):
            raise ValueError()
        if p.scheme == 'http' and not ipaddress.ip_address(p.hostname).is_loopback:
            raise ValueError()
        return p
    except (ValueError, TypeError):
        raise CyberError('invalid_supplier_endpoint') from None


def artifact_bytes(record):
    """Verify and decode saved output as data; acceptance and execution are separate."""
    try:
        item = record['artifact']
        if (set(item) != {'encoding', 'sha256', 'content', 'execution_authorized'}
                or item['encoding'] != 'base64' or item['execution_authorized'] is not False
                or type(item['content']) is not str or len(item['content']) > 43692
                or type(item['sha256']) is not str or re.fullmatch('[a-f0-9]{64}', item['sha256']) is None):
            raise ValueError()
        raw = base64.b64decode(item['content'], validate=True)
        if not 1 <= len(raw) <= 32768 or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError()
        return raw
    except (TypeError, ValueError, KeyError, binascii.Error):
        raise CyberError('supplier_artifact_mismatch') from None


class SupplierClient:
    """One-request operations for the experimental invited, zero-price job flow.

    Use ``request(operation, **fields)`` with an operation from OPERATIONS. Its
    return value is the server's record, not independent proof of work/payment.
    Payment reads return retained participant records without refreshing chain
    observations. No method provisions accounts, admits suppliers, moves funds
    or makes provider calls.
    """
    def __init__(self, *, base_url, token_file, timeout=30):
        self._endpoint = _endpoint(base_url)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not .1 <= timeout <= 60):
            raise CyberError('invalid_http_timeout')
        self._token_file = token_file
        self.timeout = timeout
        self._attempted = set()
        self._lock = threading.Lock()

    def request(self, operation, **fields):
        if type(operation) is not str or operation not in OPERATIONS:
            raise CyberError('invalid_supplier_operation')
        route, names = OPERATIONS[operation]
        if set(fields) != set(names):
            raise CyberError('invalid_supplier_request')
        try:
            payload = _encode(fields)
            if len(payload) > MAX_REQUEST:
                raise CyberError('request_size_limit')
            body = _decode(payload)  # use one immutable serialized request snapshot
            for name, value in body.items():
                if name.endswith('_id') or name == 'after' and value is not None:
                    if type(value) is not str or re.fullmatch('[A-Za-z0-9_-]{1,128}', value) is None:
                        raise ValueError()
                if name.endswith('_sha256'):
                    if type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None:
                        raise ValueError()
            if 'source' in body and (type(body['source']) is not str
                    or not 1 <= len(body['source'].encode('utf-8')) <= 32768):
                raise ValueError()
            if 'limit' in body and (type(body['limit']) is not int or not 1 <= body['limit'] <= 20):
                raise ValueError()
            if operation == 'register_service':
                service_id = body['manifest']['id']
                if type(service_id) is not str or re.fullmatch('[A-Za-z0-9_-]{1,128}', service_id) is None:
                    raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise CyberError('invalid_supplier_request') from None
        try:
            token = _token(self._token_file)
        except CyberError:
            raise CyberError('private_supplier_token_required') from None
        if re.fullmatch('cyber_[A-Za-z0-9_-]{43}', token) is None:
            raise CyberError('private_supplier_token_required')
        if operation in ONCE:
            key = (operation, body[ONCE[operation]])
            with self._lock:
                if key in self._attempted or len(self._attempted) >= 1024:
                    raise CyberError('supplier_review_required_read_status')
                # Mark before any network activity; even interruption cannot
                # turn an uncertain submission into a second implicit attempt.
                self._attempted.add(key)
        value = post_json(self._endpoint, self._endpoint.path.rstrip('/') + '/' + route,
            payload, token, timeout=self.timeout, prefix='supplier',
            max_response=MAX_RESPONSE, success_statuses=(200,))
        try:
            if set(value) != {'schema', 'record'} or value['schema'] != SCHEMA or type(value['record']) is not dict:
                raise ValueError()
            record = value['record']
            identity = ('job_id' if 'job_id' in body else
                        'service_id' if 'service_id' in body else
                        'offer_id' if 'offer_id' in body else
                        'challenge_id' if 'challenge_id' in body else None)
            observed = record['challenge'] if operation == 'read_challenge' else record
            if identity is not None and observed.get(identity) != body[identity]:
                raise ValueError()
            if operation == 'register_service' and record.get('service_id') != service_id:
                raise ValueError()
            if operation in ('read_job', 'job_output', 'deliver_job'):
                if record.get('settlement') != 'disabled' or record.get('purchase_proven') is not False:
                    raise ValueError()
            if operation == 'claim_job' and record.get('provider_calls_authorized') is not False:
                raise ValueError()
            if operation == 'read_payment':
                schema = record.get('schema')
                if (schema not in ('cyber-agent-payment-contract/1', 'cyber-agent-payment-contract/2')
                        or type(record.get('grant_sha256')) is not str
                        or re.fullmatch('[a-f0-9]{64}', record['grant_sha256']) is None
                        or record.get('settlement') != 'disabled'
                        or record.get('spending_authorized') is not False
                        or record.get('automatic_retry') is not False
                        or type(record.get('caps')) is not dict):
                    raise ValueError()
                caps = record['caps']
                if schema.endswith('/1'):
                    if caps.get('chain') != 'regtest' or caps.get('asset') != 'BTC_REGTEST':
                        raise ValueError()
                elif (caps.get('schema') != 'cyber-ethereum-payment-caps/1'
                        or caps.get('chain') != 'ethereum' or 'transaction_settlement' in record):
                    raise ValueError()
                observation = record.get('transaction_settlement')
                if observation is not None and (type(observation) is not dict
                        or observation.get('schema') != 'cyber-agent-settlement-status/1'
                        or observation.get('job_id') != body['job_id']
                        or observation.get('chain') != 'regtest' or observation.get('asset') != 'BTC_REGTEST'
                        or observation.get('live_settlement') != 'disabled'
                        or observation.get('spending_authorized') is not False
                        or observation.get('automatic_retry') is not False
                        or observation.get('retry_authorized') is not False):
                    raise ValueError()
            if operation == 'job_output':
                artifact_bytes(record)
            return record
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CyberError('supplier_response_binding_mismatch') from None

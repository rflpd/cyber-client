"""Explicit invited buyer/supplier operations; no default deployment or retries.

The caller durably retains request IDs and intent before mutations. Server-side
accounts, qualification, permission admission, capacity and budgets remain the
authority. This client neither generates nor executes returned source.
"""
import base64
import binascii
import hashlib
import ipaddress
import json
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
    'read_balance': ('balances/read', ('wallet_id',)),
    'read_balance_job': ('balance-jobs/read', ('job_id',)),
    'consent_balance_job': ('balance-jobs/consent', ('job_id', 'authorization_sha256')),
    'reserve_balance_job': ('balance-jobs/reserve', ('job_id', 'offer_id', 'scope_sha256', 'quote_sha256')),
    'job_output': ('jobs/output', ('job_id',)),
    'job_offers': ('jobs/offers', ('job_id', 'after', 'limit')),
    'reserve_job': ('jobs/reserve', ('job_id', 'offer_id', 'scope_sha256')),
    'cancel_job': ('jobs/cancel', ('job_id',)),
    'declare_capacity': ('suppliers/capacity/declare', ('slots', 'own_reserved', 'expires_at')),
    'assigned_jobs': ('suppliers/jobs/list', ('service_id', 'after', 'limit')),
    'claim_job': ('suppliers/jobs/claim', ('job_id',)),
    'deliver_job': ('suppliers/jobs/deliver', ('job_id', 'source')),
}
ONCE = {'submit_answer': 'challenge_id', 'claim_job': 'job_id', 'deliver_job': 'job_id',
        'consent_balance_job': 'job_id', 'reserve_balance_job': 'job_id'}


def _units(value):
    if (type(value) is not str or len(value) > 78
            or re.fullmatch(r'0|[1-9][0-9]*', value) is None or int(value) >= 2**256):
        raise ValueError()
    return int(value)


def _usdc(value):
    if (type(value) is not dict or value != {'schema':'cyber-ethereum-token/1',
            'chain_id':1, 'symbol':'USDC', 'contract':'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
            'decimals':6} or type(value['chain_id']) is not int or type(value['decimals']) is not int):
        raise ValueError()


def _hex(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None


def _id(value):
    return type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',value) is not None


def _balance_authorization(value, job_id):
    _usdc(value['token'])
    if (value['schema']!='cyber-balance-job/1' or value['job_id']!=job_id
            or value['funding_route']!='reusable_account_balance' or value['ledger_schema']!='cyber-account-balances/1'
            or value['invoice_funding_required'] is not False or value['sender_required'] is not False
            or value['job_network_fee_wei']!='0' or type(value['job_chain_transactions']) is not int
            or value['job_chain_transactions']!=0 or value['original_gas_allowance_charged'] is not False
            or value['supplier_credit']!='internal_available_balance'
            or value['refund_policy']!='definite_unearned_hold_restored_in_full'
            or value['withdrawal_policy']!='separate_explicit_request_and_fee'
            or any(not _hex(value[key]) for key in ('scope_sha256','contract_sha256','grant_sha256',
                'quote_sha256','ledger_source_sha256','offer_sha256'))
            or any(not _id(value[key]) for key in ('credential_id','issuer_id','service_id','offer_id'))
            or any(type(value[key]) is not int or not 0<=value[key]<=8640000000000
                for key in ('created_at','expires_at','booking_deadline'))
            or not value['created_at']<min(value['expires_at'],value['booking_deadline'])
            or type(value['maximum_job_value_microusd']) is not int or value['maximum_job_value_microusd']<=0):
        raise ValueError()
    wallets=value['wallets']
    if type(wallets) is not dict or set(wallets)!={'buyer','supplier','platform'}:raise ValueError()
    for wallet in wallets.values():
        if (type(wallet) is not dict or set(wallet)!={'id','owner_id','wallet_sha256'}
                or not _id(wallet['id']) or not _id(wallet['owner_id']) or not _hex(wallet['wallet_sha256'])):
            raise ValueError()
    if len({wallet['id'] for wallet in wallets.values()})!=3:raise ValueError()
    if _units(value['principal_units'])<=0:raise ValueError()
    _units(value['unused_original_gas_allowance_wei'])


def _balance_record(operation, record, body):
    if operation == 'read_balance':
        _usdc(record['token'])
        buckets=record['balances_units']
        if (record['schema']!='cyber-account-balances/1' or type(buckets) is not dict
                or set(buckets)!={'available','job_reserved','withdrawal_reserved'}
                or type(record['review_required']) is not bool or record['internal_accounting'] is not True
                or record['custody_verified'] is not False or record['live_settlement']!='disabled'
                or record['automatic_retry'] is not False):
            raise ValueError()
        for value in buckets.values():_units(value)
        if _units(record['spendable_units']) != (0 if record['review_required'] else _units(buckets['available'])):
            raise ValueError()
        if type(record['backing']) is not dict or record['backing'].get('custody_verified') is not False:
            raise ValueError()
        source=record['source'];backing=record['backing']
        if (type(source) is not dict or set(source)!={'schema','source_id'}
                or source['schema']!='cyber-account-balance-source/1' or not _hex(source['source_id'])
                or backing.get('status') not in {'covered','observation_required','observation_pending',
                    'stale','unknown','restricted','deficit','invalid_records'}
                or type(backing.get('held')) is not bool
                or type(backing.get('source_review_required',False)) is not bool
                or backing['status']!='covered' and not backing['held']
                or backing['held'] and not record['review_required']
                or backing.get('source_review_required',False) and not backing['held']
                or backing['status']=='covered' and not _hex(backing.get('result_sha256'))):raise ValueError()
    elif operation == 'read_balance_job':
        authorization=record['authorization'];_balance_authorization(authorization,body['job_id'])
        digest=hashlib.sha256(json.dumps(authorization,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if (record['schema']!='cyber-balance-job/1' or authorization['schema']!='cyber-balance-job/1'
                or authorization['job_id']!=body['job_id'] or record['authorization_sha256']!=digest
                or record['funding_route']!='reusable_account_balance' or record['invoice_funding_required'] is not False
                or record['sender_required'] is not False or record['job_chain_transactions']!=0
                or type(record['job_chain_transactions']) is not int or record['job_network_fee_wei']!='0'
                or record['live_settlement']!='disabled' or record['automatic_retry'] is not False
                or any(type(record[key]) is not bool for key in ('consented','funded_booking','hold_consumed','review_required'))):
            raise ValueError()
        if _units(authorization['principal_units'])+_units(authorization['platform_fee_units'])!=_units(authorization['hold_units']):
            raise ValueError()
        reserved=_units(record['current_job_reserved_units'])
        if (record['funded_booking'] and not record['consented']
                or record['hold_id']!=('job-'+digest if record['funded_booking'] else None)):raise ValueError()
        if reserved != (int(authorization['hold_units']) if record['funded_booking'] and not record['hold_consumed'] else 0):
            raise ValueError()
        settlement=record['internal_settlement']
        if (settlement is not None)!=record['hold_consumed']:raise ValueError()
        if settlement is not None:
            if (not record['funded_booking'] or settlement['job_id']!=body['job_id']
                    or settlement['authorization_sha256']!=digest or settlement['hold_id']!=record['hold_id']
                    or settlement['amount_units']!=authorization['hold_units']
                    or settlement['onchain_transaction'] is not False or settlement['internal_settlement'] is not True
                    or settlement['new_deposit_credit_created'] is not False or settlement['live_settlement']!='disabled'):
                raise ValueError()
            amounts=tuple(_units(settlement[key]) for key in ('supplier_credit_units','platform_credit_units','buyer_restored_units'))
            expected={'earned':(int(authorization['principal_units']),int(authorization['platform_fee_units']),0),
                      'refund':(0,0,int(authorization['hold_units']))}
            if amounts!=expected.get(settlement['disposition']):raise ValueError()
    elif operation == 'consent_balance_job':
        if (record['authorization_sha256']!=body['authorization_sha256'] or record['settlement']!='disabled'
                or type(record['replayed']) is not bool or record['permission_reserved_again'] is not False
                or record['funds_reserved'] is not False):raise ValueError()
    elif operation == 'reserve_balance_job':
        if (record['quote_sha256']!=body['quote_sha256'] or record['funding_route']!='reusable_account_balance'
                or record['settlement']!='disabled' or record['reserved'] is not True
                or type(record['replayed']) is not bool or not _hex(record['authorization_sha256'])
                or record['new_execution_authority'] is not False):raise ValueError()


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
    """One-request operations for explicitly enabled invited participant flows.

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
                        'wallet_id' if 'wallet_id' in body else
                        'service_id' if 'service_id' in body else
                        'offer_id' if 'offer_id' in body else
                        'challenge_id' if 'challenge_id' in body else None)
            observed = record['challenge'] if operation == 'read_challenge' else record
            if identity is not None and observed.get(identity) != body[identity]:
                raise ValueError()
            if operation in ('read_balance','read_balance_job','consent_balance_job','reserve_balance_job'):
                _balance_record(operation,record,body)
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
                if record.get('balance_job') is not None:
                    balance=record['balance_job']
                    _balance_record('read_balance_job',balance,body)
                    if (schema!='cyber-agent-payment-contract/2'
                            or balance['authorization']['grant_sha256']!=record['grant_sha256']
                            or balance['authorization']['token']!=caps['token']
                            or record.get('invoice') is not None or record['invoice_funding_required'] is not False
                            or type(record['job_chain_transactions']) is not int or record['job_chain_transactions']!=0
                            or record['job_network_fee_wei']!='0'):
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

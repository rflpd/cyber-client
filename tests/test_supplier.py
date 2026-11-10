"""Synthetic HTTP/credential fixtures only; no deployed service or model calls."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cyber_client import CyberError
from cyber_client.supplier import SupplierClient, SCHEMA, artifact_bytes

TOKEN = 'cyber_' + 's' * 43


def payment_record():
    return {'schema':'cyber-agent-payment-contract/1', 'job_id':'j', 'grant_sha256':'a'*64,
        'caps':{'chain':'regtest','asset':'BTC_REGTEST'}, 'settlement':'disabled',
        'spending_authorized':False, 'automatic_retry':False, 'funding':'unknown',
        'transaction_settlement':{'schema':'cyber-agent-settlement-status/1','job_id':'j',
            'chain':'regtest','asset':'BTC_REGTEST','live_settlement':'disabled',
            'spending_authorized':False,'automatic_retry':False,'retry_authorized':False,
            'send_attempt':'unknown','status':'confirmation_stale','currently_confirmed':False,
            'receipt':{'fee_sat':2000,'outputs_sat':{'supplier':90000}},'complete_delivery_cost_microusd':None}}


class SupplierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests, cls.responses = [], []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                cls.requests.append((self.path, self.headers['Authorization'], json.loads(raw)))
                status, value, headers = cls.responses.pop(0)
                if status is None:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                for key, val in headers.items(): self.send_header(key, val)
                self.end_headers()
                self.wfile.write(body)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': .01})
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port) + '/v1'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=5)
        assert not cls.thread.is_alive()

    def setUp(self):
        type(self).requests = []; type(self).responses = []
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.token = Path(self.temp.name) / 'token'
        self.token.write_text(TOKEN); self.token.chmod(0o600)
        self.client = SupplierClient(base_url=self.base, token_file=self.token)

    def add(self, record, status=200, headers=None):
        self.responses.append((status, {'schema': SCHEMA, 'record': record}, headers or {}))

    def test_requirements_and_pagination_are_returned_without_followup_requests(self):
        requirements = {'execution_authorized': False, 'acceptance': {'required': 61}}
        self.add({'challenge': {'challenge_id': 'c', 'status': 'awaiting_submission'},
                  'requirements': requirements, 'task': 'Synthetic task'})
        result = self.client.request('read_challenge', challenge_id='c')
        self.assertEqual(result['requirements'], requirements)
        self.add({'job_id': 'j', 'offers': [], 'next_cursor': 'ineligible', 'settlement': 'disabled'})
        result = self.client.request('job_offers', job_id='j', after=None, limit=20)
        self.assertEqual(result['next_cursor'], 'ineligible')
        self.assertEqual(len(self.requests), 2)

    def test_each_write_is_explicit_and_preserves_bound_ids(self):
        calls = [
            ('register_service', {'manifest': {'id': 's'}, 'configuration_sha256': 'a'*64}, {'service_id': 's'}),
            ('read_service', {'service_id': 's'}, {'service_id': 's'}),
            ('submit_answer', {'challenge_id': 'c', 'binding_sha256': 'b'*64, 'source': 'synthetic'}, {'challenge_id': 'c'}),
            ('declare_offer', {'offer_id': 'o', 'challenge_id': 'c', 'price_microusd': 0, 'slots': 1, 'expires_at': 2000000000}, {'offer_id': 'o'}),
            ('read_offer', {'offer_id': 'o'}, {'offer_id': 'o'}),
            ('declare_capacity', {'slots': 1, 'own_reserved': 0, 'expires_at': 2000000000}, {'declared_slots': 1}),
            ('create_job', {'job_id': 'j', 'deadline': 2000000000}, {'job_id': 'j'}),
            ('reserve_job', {'job_id': 'j', 'offer_id': 'o', 'scope_sha256': 'c'*64}, {'job_id': 'j'}),
            ('assigned_jobs', {'service_id': 's', 'after': None, 'limit': 20}, {'service_id': 's', 'jobs': []}),
            ('claim_job', {'job_id': 'j'}, {'job_id': 'j', 'provider_calls_authorized': False}),
            ('deliver_job', {'job_id': 'j', 'source': 'synthetic'}, {'job_id': 'j', 'job_accepted': False,
                'settlement': 'disabled', 'purchase_proven': False}),
            ('cancel_job', {'job_id': 'j'}, {'job_id': 'j', 'capacity_held_for_uncertain_work': True}),
        ]
        for operation, fields, record in calls:
            self.add(record)
            self.assertEqual(self.client.request(operation, **fields), record)
            self.assertEqual(self.requests[-1][2], fields)
        self.assertEqual(len(self.requests), len(calls))
        self.assertTrue(all(row[1] == 'Bearer ' + TOKEN for row in self.requests))

    def test_lost_claim_answer_and_delivery_never_retry(self):
        for operation, fields in (
            ('claim_job', {'job_id': 'j'}),
            ('submit_answer', {'challenge_id': 'c', 'binding_sha256': 'b'*64, 'source': 'synthetic'}),
            ('deliver_job', {'job_id': 'j', 'source': 'synthetic'}),
        ):
            self.responses.append((None, None, {}))
            with self.assertRaisesRegex(CyberError, 'outcome_unknown'):
                self.client.request(operation, **fields)
            before = len(self.requests)
            with self.assertRaisesRegex(CyberError, 'read_status'):
                self.client.request(operation, **fields)
            self.assertEqual(len(self.requests), before)
        self.add({'service_id': 's', 'jobs': [{'job_id': 'j', 'claimed': True, 'outcome': None}]})
        self.assertIsNone(self.client.request('assigned_jobs', service_id='s', after=None, limit=20)['jobs'][0]['outcome'])

    def test_concurrent_claim_makes_one_request(self):
        from concurrent.futures import ThreadPoolExecutor
        self.add({'job_id': 'j', 'provider_calls_authorized': False})
        def claim():
            try: return self.client.request('claim_job', job_id='j')['job_id']
            except CyberError as e: return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(results), ['j', 'supplier_review_required_read_status'])
        self.assertEqual(len(self.requests), 1)

    def test_artifact_digest_and_no_execution_flag_are_checked(self):
        raw = b'synthetic output, never executed'
        record = {'job_id': 'j', 'settlement': 'disabled', 'purchase_proven': False,
                  'outcome': {'job_accepted': False}, 'artifact': {
                      'encoding': 'base64', 'sha256': hashlib.sha256(raw).hexdigest(),
                      'content': base64.b64encode(raw).decode(), 'execution_authorized': False}}
        self.add(record)
        got = self.client.request('job_output', job_id='j')
        self.assertEqual(artifact_bytes(got), raw)
        self.assertIs(got['outcome']['job_accepted'], False)
        for change in ({'sha256': '0'*64}, {'execution_authorized': True}, {'content': '!!!'}):
            self.add(dict(record, artifact=dict(record['artifact'], **change)))
            with self.assertRaisesRegex(CyberError, 'artifact_mismatch'):
                self.client.request('job_output', job_id='j')

    def test_identity_payment_and_provider_authority_mismatch_are_rejected(self):
        for change in ({'job_id': 'other'}, {'settlement': 'paid'}, {'purchase_proven': True}):
            self.add(dict({'job_id': 'j', 'settlement': 'disabled', 'purchase_proven': False}, **change))
            with self.assertRaisesRegex(CyberError, 'binding_mismatch'):
                self.client.request('read_job', job_id='j')
        self.add({'job_id': 'j', 'provider_calls_authorized': True})
        with self.assertRaisesRegex(CyberError, 'binding_mismatch'):
            self.client.request('claim_job', job_id='j')

    def test_http_errors_are_fixed_and_redirect_is_not_followed(self):
        for status, expected in ((302, 'redirect_refused'), (401, 'unauthorized'),
                                 (403, 'unauthorized'), (409, 'supplier_review_required'),
                                 (503, 'supplier_http_error')):
            self.responses.append((status, {'error': 'private server diagnostic ' + TOKEN},
                                   {'Location': 'https://example.invalid/steal'}))
            with self.assertRaises(CyberError) as caught:
                self.client.request('read_service', service_id='s')
            self.assertEqual(str(caught.exception), expected)
            self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(len(self.requests), 5)

    def test_strict_json_size_schema_and_proxies(self):
        for value in (b'{"schema":"x","schema":"y"}', b'{"x":NaN}',
                      {'schema': 'different', 'record': {}}, b'x'*131073):
            self.responses.append((200, value, {}))
            with self.assertRaises(CyberError): self.client.request('read_service', service_id='s')
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1'}):
            self.add({'service_id': 's'})
            self.assertEqual(self.client.request('read_service', service_id='s')['service_id'], 's')

    def test_bad_request_endpoint_and_credential_send_nothing(self):
        for url in ('http://example.com/v1', 'http://localhost/v1', 'https://host/v2',
                    'https://user:pass@host/v1', 'https://host/../v1', 'https://host/v1?token=x'):
            with self.assertRaises(CyberError): SupplierClient(base_url=url, token_file=self.token)
        for operation, fields in (('bootstrap', {}), ('read_service', {'service_id': '../s'}),
                                  ('claim_job', {'job_id': 'j', 'accepted': True}),
                                  ('job_offers', {'job_id': 'j', 'after': None, 'limit': True}),
                                  ('deliver_job', {'job_id': 'j', 'source': 'x'*32769})):
            with self.assertRaises(CyberError): self.client.request(operation, **fields)
        self.token.chmod(0o644)
        with self.assertRaisesRegex(CyberError, 'private_supplier_token'):
            self.client.request('read_service', service_id='s')
        self.token.chmod(0o600); self.token.write_text('wrong_' + 'x'*43)
        with self.assertRaisesRegex(CyberError, 'private_supplier_token'):
            self.client.request('read_service', service_id='s')
        self.assertEqual(self.requests, [])

    def test_payment_reads_preserve_unknown_and_stale_observations_without_polling(self):
        expected=payment_record()
        for _ in range(2):
            self.add(expected)
            self.assertEqual(self.client.request('read_payment',job_id='j'),expected)
        self.assertEqual(len(self.requests),2)
        self.assertTrue(all(path=='/v1/payments/read' and body=={'job_id':'j'}
                            for path,_,body in self.requests))
        self.assertEqual(self.client._attempted,set())

    def test_payment_token_preparation_keeps_large_decimal_strings_exact(self):
        record=payment_record();record.pop('transaction_settlement')
        record.update(schema='cyber-agent-payment-contract/2',caps={
            'schema':'cyber-ethereum-payment-caps/1','chain':'ethereum',
            'maximum_total_gas_wei':'30000000000000000'},
            token_quote={'maximum_total_gas_wei':'25000000000000000','actual_settlement_gas_wei':None})
        self.add(record)
        self.assertEqual(self.client.request('read_payment',job_id='j'),record)
        self.assertEqual(len(self.requests),1)

    def test_payment_authority_and_job_mismatches_are_rejected(self):
        changes=({'job_id':'other'},{'schema':'future-schema'}, {'grant_sha256':'invalid'},
            {'settlement':'enabled'},{'spending_authorized':True},{'automatic_retry':0},
            {'caps':None},{'caps':{'chain':'main','asset':'BTC'}},
            {'schema':'cyber-agent-payment-contract/2','caps':{'schema':'cyber-ethereum-payment-caps/1','chain':'ethereum'}})
        for change in changes:
            with self.subTest(change=tuple(change)):
                self.add(payment_record()|change)
                with self.assertRaisesRegex(CyberError,'supplier_response_binding_mismatch'):
                    self.client.request('read_payment',job_id='j')
        self.assertEqual(len(self.requests),len(changes))

    def test_nested_payment_observation_cannot_change_job_chain_or_authority(self):
        changes=({'job_id':'other'},{'schema':'future-schema'},{'chain':'main'}, {'asset':'BTC'},
            {'live_settlement':'enabled'},{'spending_authorized':True}, {'automatic_retry':True},
            {'retry_authorized':True})
        for change in changes:
            record=payment_record();record['transaction_settlement'].update(change)
            self.add(record)
            with self.assertRaisesRegex(CyberError,'supplier_response_binding_mismatch'):
                self.client.request('read_payment',job_id='j')
        self.assertEqual(len(self.requests),len(changes))

    def test_payment_transport_failure_is_one_request_and_errors_hide_server_body(self):
        for status in (None,404,403,503):
            self.responses.append((status, {'error':'private diagnostic '+TOKEN}, {}))
            before=len(self.requests)
            with self.assertRaises(CyberError) as caught:self.client.request('read_payment',job_id='j')
            self.assertNotIn(TOKEN,str(caught.exception))
            self.assertEqual(len(self.requests),before+1)
        self.assertEqual(self.client._attempted,set())

    def test_payment_read_has_closed_fields_and_no_payment_mutations(self):
        for fields in ({},{'job_id':True},{'job_id':'../j'},{'job_id':'j','retry':True}):
            with self.assertRaises(CyberError):self.client.request('read_payment',**fields)
        for operation in ('authorize_payment','reserve_funding','sign_payment','send_payment','reconcile_payment'):
            with self.assertRaises(CyberError):self.client.request(operation,job_id='j')
        self.assertEqual(self.requests,[])


if __name__ == '__main__': unittest.main()

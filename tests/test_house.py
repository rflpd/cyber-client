"""Synthetic HTTP only: no model, provider, real credential or execution service."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cyber_client import CyberError
from cyber_client.house import HouseClient, _token
from cyber_client import house_contract as c

TOKEN = 'synthetic_' + 'x' * 40

def request():
    return dict(schema=c.SCHEMA, request_id='synthetic-001', profile_id='qwen35-9b-q4km-b12078/1',
                task_contract_sha256='a'*64, resource_contract_sha256='b'*64,
                prompt='Synthetic test only', max_output_tokens=16)

def quote(body, state='quoted'):
    r = c.request_record(body)
    return dict(quote_id='c'*32, request_id=r['request_id'], model=r['model'],
                max_output_tokens=r['max_output_tokens'], expires_at=2000000000, state=state,
                request_sha256=hashlib.sha256(json.dumps(r).encode()).hexdigest(), charged_microusd=0,
                settlement='disabled', semantic_acceptance=False, request_schema=r['schema'],
                **{key:r[key] for key in c._BOUND_FIELDS if key!='request_schema'})

def result(body, q):
    r = c.request_record(body); p=r['profile']
    usage=dict(state='completed', generation_complete=True, model_executed=True, cleanup_verified=True,
               synthetic_response_complete=False, semantic_acceptance=False, provider_calls=0,
               profile_id=r['model'], profile_sha256=r['profile_sha256'], actual_model=p['model'],
               model_sha256=p['model_sha256'],runtime_sha256=p['runtime_sha256'],template_sha256=p['template_sha256'],
               task_contract_sha256=r['task_contract_sha256'],resource_contract_sha256=r['resource_contract_sha256'],
               prompt_bytes=len(r['prompt'].encode()),max_output_tokens=r['max_output_tokens'],stop_type='eos',
               reasoning_filtered=False,tokens_predicted=1,tokens_evaluated=5,rendered_prompt_sha256='d'*64)
    return dict(quote_id=q['quote_id'],state='completed',model=r['model'],output='SYNTHETIC ANSWER',usage=usage,
                charged_microusd=0,settlement='disabled',semantic_acceptance=False,
                **{key:q[key] for key in c._BOUND_FIELDS})

def wrap(record):return dict(api_schema=c.API_SCHEMA,record=record)

def cancellation(q, state='quoted'):
    requested=state in ('quoted','running')
    return dict(schema='cyber-house-cancellation/1',quote_id=q['quote_id'],
        request_sha256=q['request_sha256'],requested=requested,
        state_at_request=state if requested else None,requested_at=1794040000 if requested else None,
        last_observed_state=state,execution_prevented=state=='quoted',cleanup_verified=False,
        retry_authorized=False,charged_microusd=0,settlement='disabled',semantic_acceptance=False)

@contextmanager
def delayed_house_server(saved_quote, saved_result, *, delay=.2, trickle=False):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            calls.append(self.path)
            try:
                if self.path.endswith('/quotes'):
                    value=wrap(saved_quote)
                else:
                    if trickle:
                        for byte in b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n':
                            self.connection.sendall(bytes([byte]));time.sleep(.03)
                        return
                    time.sleep(delay);value=wrap(saved_result)
                raw=json.dumps(value).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            except OSError:pass
    class Server(ThreadingHTTPServer):
        daemon_threads=False
        def handle_error(self,*args):pass
    server=Server(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
    try:yield 'http://127.0.0.1:%d/v1/house'%server.server_port,calls
    finally:server.shutdown();server.server_close();thread.join()

class HouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                cls.requests.append((self.path,self.headers.get('Authorization'),json.loads(raw)))
                status,body,headers=cls.responses.pop(0)
                if status is None:
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                raw=body if isinstance(body,bytes) else json.dumps(body).encode()
                self.send_response(status)
                headers=dict({'Content-Type':'application/json','Content-Length':str(len(raw))},**headers)
                for key,val in headers.items():self.send_header(key,val)
                self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:%d/v1/house'%cls.server.server_port
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        type(self).requests=[];type(self).responses=[]
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.token=Path(self.temp.name)/'token';self.token.write_text(TOKEN);self.token.chmod(0o600)
        self.client=HouseClient(base_url=self.base,token_file=self.token)
        self.body=request();self.q=quote(self.body);self.answer=result(self.body,self.q)
    def add(self,body,status=200,headers=None):self.responses.append((status,body,headers or {}))
    def job(self):
        self.add(wrap(self.q));return self.client.quote(self.body)
    def test_full_http_flow(self):
        job=self.job();self.add(wrap(dict(self.q,state='running')),202)
        self.assertEqual(self.client.execute(job)['state'],'running')
        self.add(wrap(self.answer));saved=self.client.status(job)
        self.assertEqual(saved['output'],'SYNTHETIC ANSWER');self.assertIs(saved['semantic_acceptance'],False)
        self.assertEqual(saved['settlement'],'disabled')
        self.assertEqual([r[0] for r in self.requests],['/v1/house/quotes','/v1/house/execute','/v1/house/status'])
        self.assertTrue(all(r[1]=='Bearer '+TOKEN for r in self.requests))
        summary = self.client.summary(job)
        self.assertEqual(summary['last_validated_state'], 'completed')
        self.assertTrue(summary['validated_output_received'])
        self.assertFalse(summary['semantic_acceptance'])
        self.assertFalse(summary['independent_model_attestation'])
        self.assertEqual(summary['received_output_sha256'], hashlib.sha256(b'SYNTHETIC ANSWER').hexdigest())
    def test_lost_execute_acknowledgement_reconciles_without_retry(self):
        job=self.job();self.responses.append((None,None,{}))
        with self.assertRaisesRegex(CyberError,'outcome_unknown'):self.client.execute(job)
        summary = self.client.summary(job)
        self.assertEqual(summary['observation'], 'execution_outcome_unknown')
        self.assertEqual(summary['last_validated_state'], 'quoted')
        self.assertTrue(summary['execution_attempted_in_this_client'])
        self.assertFalse(summary['new_execution_authorized'])
        with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)
        new=HouseClient(base_url=self.base,token_file=self.token)
        restored=new.restore(job.request,job.quote)
        self.assertEqual(new.summary(restored)['observation'], 'restored_records')
        self.assertTrue(new.summary(restored)['restored_without_execution_authority'])
        with self.assertRaisesRegex(CyberError,'review_required'):new.execute(restored)
        self.add(wrap(self.answer));self.assertEqual(new.status(restored)['state'],'completed')
        self.assertEqual(new.summary(restored)['observation'], 'validated_response')
        self.assertEqual(sum(r[0].endswith('/execute') for r in self.requests),1)
    def test_restore_validates_and_makes_no_request(self):
        job=self.client.restore(self.body,self.q)
        self.assertEqual(job.quote_id,self.q['quote_id']);self.assertEqual(self.requests,[])
        with self.assertRaises(CyberError):self.client.restore(dict(self.body,prompt='changed'),self.q)

    def test_cancel_http_ack_is_distinct_from_execution_and_cleanup(self):
        job=self.job();receipt=cancellation(self.q)
        self.add(wrap(receipt));self.assertEqual(self.client.cancel(job),receipt)
        self.assertEqual([r[0] for r in self.requests],['/v1/house/quotes','/v1/house/cancel'])
        self.assertEqual(self.requests[-1][2],{'quote_id':self.q['quote_id']})
        with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)
        summary=self.client.summary(job)
        self.assertEqual(summary['schema'],'cyber-house-client-status/2')
        self.assertEqual(summary['cancellation_observation'],'validated_acknowledgement')
        self.assertTrue(summary['last_cancellation_requested'])
        self.assertFalse(summary['cancellation_acknowledges_cleanup'])
        self.assertFalse(summary['execution_attempted_in_this_client'])
        self.assertFalse(summary['semantic_acceptance'])

    def test_restored_job_can_request_stop_but_cannot_execute(self):
        job=self.client.restore(self.body,self.q);self.add(wrap(cancellation(self.q,'running')))
        receipt=self.client.cancel(job)
        self.assertFalse(receipt['execution_prevented'])
        self.assertTrue(self.client.summary(job)['restored_without_execution_authority'])
        with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)
        self.assertEqual([r[0] for r in self.requests],['/v1/house/cancel'])

    def test_lost_cancel_ack_never_reauthorizes_execute(self):
        job=self.job();self.responses.append((None,None,{}))
        with self.assertRaises(CyberError):self.client.cancel(job)
        self.assertEqual(self.client.summary(job)['cancellation_observation'],'outcome_unknown')
        self.assertIsNone(self.client.summary(job)['last_cancellation_requested'])
        with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)
        self.assertEqual(sum(r[0].endswith('/cancel') for r in self.requests),1)

    def test_cancel_interrupts_preserve_unknown_outcome_and_execution_fence(self):
        for error in (KeyboardInterrupt,SystemExit,RuntimeError):
            with self.subTest(error=error.__name__):
                job=self.job()
                with patch.object(self.client,'_call',side_effect=error):
                    with self.assertRaises(error):self.client.cancel(job)
                self.assertEqual(self.client.summary(job)['cancellation_observation'],'outcome_unknown')
                with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)

    def test_cancel_rejects_changed_identity_privilege_or_false_cleanup(self):
        mutations=[{'quote_id':'e'*32},{'request_sha256':'f'*64},{'cleanup_verified':True},
            {'retry_authorized':True},{'charged_microusd':1},{'settlement':'paid'},
            {'semantic_acceptance':True},{'requested':1},{'requested_at':True},
            {'execution_prevented':1},{'last_observed_state':'completed'},
            {'state_at_request':'running'},{'extra':'ignored'}]
        for change in mutations:
            with self.subTest(fields=list(change)):
                job=self.job();self.add(wrap(dict(cancellation(self.q),**change)))
                with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.cancel(job)
                self.assertEqual(self.client.summary(job)['cancellation_observation'],'outcome_unknown')
                with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)

    def test_repeat_cancel_preserves_immutable_request_and_rejects_regression(self):
        job=self.job();receipt=cancellation(self.q,'running')
        self.add(wrap(receipt));self.client.cancel(job)
        self.add(wrap(receipt));self.assertEqual(self.client.cancel(job),receipt)
        self.add(wrap(dict(receipt,last_observed_state='interrupted')));self.client.cancel(job)
        self.add(wrap(receipt))
        with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.cancel(job)
        self.assertEqual(self.client.summary(job)['last_cancellation_state'],'interrupted')
        self.add(wrap(dict(receipt,requested_at=receipt['requested_at']+1,last_observed_state='interrupted')))
        with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.cancel(job)

    def test_terminal_job_cancel_does_not_claim_output_receipt(self):
        job=self.job();receipt=cancellation(self.q,'completed')
        self.add(wrap(receipt));self.assertFalse(self.client.cancel(job)['requested'])
        self.assertFalse(self.client.summary(job)['validated_output_received'])
        self.add(wrap(cancellation(self.q)))
        with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.cancel(job)

    def test_cancel_rejects_other_client_job_without_network(self):
        job=self.job();other=HouseClient(base_url=self.base,token_file=self.token)
        before=len(self.requests)
        with self.assertRaisesRegex(CyberError,'owner_mismatch'):other.cancel(job)
        self.assertEqual(len(self.requests),before)

    def test_status_cannot_complete_after_acknowledged_cancellation(self):
        for requested_state,observed_state in (('quoted','quoted'),('running','running'),('running','interrupted')):
            with self.subTest(requested=requested_state,observed=observed_state):
                job=self.job();receipt=cancellation(self.q,requested_state)
                receipt['last_observed_state']=observed_state
                self.add(wrap(receipt));self.client.cancel(job)
                for response in (self.answer,dict(self.q,state='completed',outcome='completed')):
                    self.add(wrap(response))
                    with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.status(job)
                    summary=self.client.summary(job)
                    self.assertFalse(summary['validated_output_received'])
                    self.assertEqual(summary['last_validated_state'],'quoted')

    def test_terminal_cancel_observation_fences_stale_status(self):
        for state in ('completed','interrupted'):
            with self.subTest(state=state):
                job=self.job();self.add(wrap(cancellation(self.q,state)));self.client.cancel(job)
                self.add(wrap(self.q))
                with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.status(job)
                self.assertEqual(self.client.summary(job)['last_cancellation_state'],state)
                good=(self.answer if state=='completed' else dict(self.q,state='interrupted',outcome='outcome_unknown'))
                self.add(wrap(good));self.client.status(job)
                self.assertEqual(self.client.summary(job)['last_validated_state'],state)

    def test_stop_before_admission_cannot_later_report_running(self):
        job=self.job();self.add(wrap(cancellation(self.q)));self.client.cancel(job)
        self.add(wrap(dict(self.q,state='running')))
        with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.status(job)

    def test_summary_is_a_private_read_only_projection_without_content(self):
        job = self.job()
        before = len(self.requests)
        summary = self.client.summary(job)
        self.assertEqual(summary['profile_id'], self.q['model'])
        self.assertEqual(summary['task_contract_sha256'], self.q['task_contract_sha256'])
        self.assertEqual(summary['resource_contract_sha256'], self.q['resource_contract_sha256'])
        self.assertEqual(summary['observation'], 'validated_quote')
        self.assertFalse(summary['validated_output_received'])
        self.assertIsNone(summary['received_output_sha256'])
        text = json.dumps(summary)
        for secret in (TOKEN, self.body['prompt'], self.answer['output']):
            self.assertNotIn(secret, text)
        summary['last_validated_state'] = 'completed'
        self.assertEqual(self.client.summary(job)['last_validated_state'], 'quoted')
        self.assertEqual(len(self.requests), before)
        other = HouseClient(base_url=self.base, token_file=self.token)
        with self.assertRaises(CyberError): other.summary(job)

    def test_completed_quote_does_not_invent_received_output(self):
        self.add(wrap(dict(self.q, state='completed')))
        job = self.client.quote(self.body)
        summary = self.client.summary(job)
        self.assertEqual(summary['last_validated_state'], 'completed')
        self.assertFalse(summary['validated_output_received'])
        self.assertFalse(summary['semantic_acceptance'])

    def test_failed_status_retains_prior_result_but_marks_observation_unavailable(self):
        job = self.job()
        self.add(wrap(self.answer)); self.client.execute(job)
        self.add({'error': 'sensitive-server-error'}, status=401)
        with self.assertRaises(CyberError): self.client.status(job)
        summary = self.client.summary(job)
        self.assertEqual(summary['observation'], 'status_unavailable')
        self.assertEqual(summary['last_validated_state'], 'completed')
        self.assertTrue(summary['validated_output_received'])
        self.assertNotIn('sensitive-server-error', json.dumps(summary))

    def test_changed_completed_output_does_not_replace_the_first_result_digest(self):
        job = self.job()
        self.add(wrap(self.answer)); self.client.execute(job)
        original = self.client.summary(job)['received_output_sha256']
        self.add(wrap(dict(self.answer, output='DIFFERENT ANSWER')))
        with self.assertRaisesRegex(CyberError, 'binding_mismatch'): self.client.status(job)
        summary = self.client.summary(job)
        self.assertEqual(summary['received_output_sha256'], original)
        self.assertEqual(summary['observation'], 'status_unavailable')

    def test_execute_interrupt_or_unexpected_exit_keeps_unknown_and_consumed(self):
        for error in (KeyboardInterrupt, SystemExit, RuntimeError):
            with self.subTest(error=error.__name__):
                client = HouseClient(base_url=self.base, token_file=self.token)
                self.add(wrap(self.q)); job = client.quote(self.body)
                with patch.object(client, '_call', side_effect=error('private-sentinel')):
                    with self.assertRaises(error): client.execute(job)
                summary = client.summary(job)
                self.assertEqual(summary['observation'], 'execution_outcome_unknown')
                self.assertTrue(summary['execution_attempted_in_this_client'])
                self.assertNotIn('private-sentinel', json.dumps(summary))
                with self.assertRaisesRegex(CyberError, 'review_required'): client.execute(job)

    def test_status_interrupt_or_unexpected_exit_marks_old_observation_unavailable(self):
        job = self.job()
        self.add(wrap(self.answer)); self.client.execute(job)
        for error in (KeyboardInterrupt, SystemExit, RuntimeError):
            with self.subTest(error=error.__name__):
                with patch.object(self.client, '_call', side_effect=error('private-sentinel')):
                    with self.assertRaises(error): self.client.status(job)
                summary = self.client.summary(job)
                self.assertEqual(summary['observation'], 'status_unavailable')
                self.assertEqual(summary['last_validated_state'], 'completed')
                self.assertTrue(summary['validated_output_received'])
    def test_request_and_quote_snapshots(self):
        job=self.job();self.body['prompt']='changed';job.request['prompt']='changed';job.quote['quote_id']='e'*32
        self.assertEqual(job.request['prompt'],'Synthetic test only');self.assertEqual(job.quote_id,'c'*32)
    def test_foreign_client_job(self):
        job=self.job();other=HouseClient(base_url=self.base,token_file=self.token)
        with self.assertRaises(CyberError):other.execute(job)
        self.assertEqual(len(self.requests),1)
    def test_concurrent_execute_submits_once(self):
        job=self.job();self.add(wrap(dict(self.q,state='running')),202);errors=[]
        def submit():
            try:self.client.execute(job)
            except CyberError as e:errors.append(e.code)
        threads=[threading.Thread(target=submit) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,['house_review_required']);self.assertEqual(len(self.requests),2)
    def test_interrupted_state_cannot_be_upgraded(self):
        job=self.job();self.add(wrap(dict(self.q,state='interrupted',outcome='outcome_unknown')))
        self.client.status(job);self.add(wrap(self.answer))
        with self.assertRaises(CyberError):self.client.status(job)
        with self.assertRaises(CyberError):self.client.execute(job)
    def test_binding_payment_and_acceptance_tampering(self):
        for key,value in [('quote_id','f'*32),('charged_microusd',1),('charged_microusd',False),
                          ('semantic_acceptance',True),('settlement','enabled'),('profile_sha256','f'*64),
                          ('task_contract_sha256','f'*64),('resource_contract_sha256','f'*64)]:
            with self.subTest(key=key,value=value):
                job=self.job();bad=copy.deepcopy(self.answer);bad[key]=value;self.add(wrap(bad))
                with self.assertRaisesRegex(CyberError,'binding_mismatch'):self.client.status(job)
    def test_usage_and_output_tampering(self):
        for key,value in [('generation_complete',False),('model_executed',False),('cleanup_verified',False),
                          ('tokens_predicted',True),('stop_type','limit'),('model_sha256','f'*64)]:
            with self.subTest(key=key):
                job=self.job();bad=copy.deepcopy(self.answer);bad['usage'][key]=value;self.add(wrap(bad))
                with self.assertRaises(CyberError):self.client.status(job)
        for output in ('','<think>hidden</think>', 'x'*8193):
            job=self.job();self.add(wrap(dict(self.answer,output=output)))
            with self.assertRaises(CyberError):self.client.status(job)
    def test_redirect_and_error_redaction(self):
        for status,expected in [(302,'redirect_refused'),(401,'unauthorized'),(409,'house_review_required'),(500,'house_http_error')]:
            self.add({'error':TOKEN},status,{'Location':'https://example.invalid/steal'})
            with self.assertRaises(CyberError) as caught:self.client.quote(self.body)
            self.assertEqual(caught.exception.code,expected);self.assertNotIn(TOKEN,str(caught.exception))
        self.assertEqual(len(self.requests),4)
    def test_malformed_or_oversized_response(self):
        for raw,headers in [(b'{"x":1,"x":2}',{}),(b'{"x":NaN}',{}),(b'[]',{}),(b'x'*262145,{}),
                            (b'{}',{'Content-Type':'text/html'}),(b'{}',{'Content-Encoding':'gzip'}),
                            (b'{}',{'Transfer-Encoding':'chunked'})]:
            self.add(raw,headers=headers)
            with self.assertRaises(CyberError):self.client.quote(self.body)
    def test_execute_has_separate_response_budget_while_status_stays_short(self):
        with delayed_house_server(self.q,self.answer) as (endpoint,calls):
            client=HouseClient(base_url=endpoint,token_file=self.token,timeout=.1,execution_timeout=.7)
            job=client.quote(self.body)
            self.assertEqual(client.execute(job)['state'],'completed')
            with self.assertRaises(CyberError):client.status(job)
            self.assertEqual(calls.count('/v1/house/execute'),1)
            self.assertEqual(client.summary(job)['last_validated_state'],'completed')

    def test_execute_response_deadline_keeps_submission_consumed(self):
        with delayed_house_server(self.q,self.answer,delay=.3) as (endpoint,calls):
            client=HouseClient(base_url=endpoint,token_file=self.token,timeout=1,execution_timeout=.1)
            job=client.quote(self.body)
            with self.assertRaises(CyberError):client.execute(job)
            with self.assertRaises(CyberError):client.execute(job)
            self.assertEqual(calls.count('/v1/house/execute'),1)
            self.assertEqual(client.summary(job)['observation'],'execution_outcome_unknown')

    def test_absolute_response_deadline_closes_trickling_headers(self):
        with delayed_house_server(self.q,self.answer,trickle=True) as (endpoint,calls):
            client=HouseClient(base_url=endpoint,token_file=self.token,timeout=.1)
            job=client.quote(self.body);started=time.monotonic()
            with self.assertRaises(CyberError):client.status(job)
            self.assertLess(time.monotonic()-started,.6)
            self.assertEqual(calls.count('/v1/house/status'),1)

    def test_execution_response_budget_is_finite_and_bounded(self):
        for timeout in (True,0,-1,float('nan'),float('inf'),361):
            with self.subTest(timeout=timeout),self.assertRaises(CyberError):
                HouseClient(base_url=self.base,token_file=self.token,execution_timeout=timeout)
        self.assertEqual(self.requests,[])

    def test_endpoint_input_rejections(self):
        for url in ('http://example.com/v1/house','https://x/v1/house?token=x','https://u:p@x/v1/house',
                    'https://x/%2e/v1/house','https://x/../v1/house','https://x/v1/other','https://x/v1/house#f'):
            with self.subTest(url=url),self.assertRaises(CyberError):HouseClient(base_url=url,token_file=self.token)
        for timeout in (True,0,float('nan'),float('inf'),61):
            with self.assertRaises(CyberError):HouseClient(base_url=self.base,token_file=self.token,timeout=timeout)
        for change in ({'max_output_tokens':True},{'prompt':'x'*8193},{'profile_id':'unknown'}):
            with self.assertRaises(CyberError):self.client.quote(dict(self.body,**change))
        self.assertEqual(self.requests,[])
    def test_token_regular_private_and_not_mutated(self):
        os.utime(self.token,(1,2)) # first read updates atime; this must remain usable.
        self.assertEqual(_token(self.token),TOKEN)
        self.token.chmod(0o644)
        with self.assertRaises(CyberError):_token(self.token)
        self.token.chmod(0o600);link=self.token.with_name('link');link.symlink_to(self.token)
        with self.assertRaises(CyberError):_token(link)
        link.unlink();os.link(self.token,link)
        with self.assertRaises(CyberError):_token(self.token)
    def test_ambient_proxy_is_ignored(self):
        self.add(wrap(self.q))
        with patch.dict(os.environ,{'http_proxy':'http://127.0.0.1:1','HTTP_PROXY':'http://127.0.0.1:1'}):
            self.client.quote(self.body)
        self.assertEqual(len(self.requests),1)

if __name__=='__main__':unittest.main()

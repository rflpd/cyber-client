"""Synthetic HTTP only: no model, provider, real credential or execution service."""
import copy
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
    def test_lost_execute_acknowledgement_reconciles_without_retry(self):
        job=self.job();self.responses.append((None,None,{}))
        with self.assertRaisesRegex(CyberError,'outcome_unknown'):self.client.execute(job)
        with self.assertRaisesRegex(CyberError,'review_required'):self.client.execute(job)
        new=HouseClient(base_url=self.base,token_file=self.token)
        restored=new.restore(job.request,job.quote)
        with self.assertRaisesRegex(CyberError,'review_required'):new.execute(restored)
        self.add(wrap(self.answer));self.assertEqual(new.status(restored)['state'],'completed')
        self.assertEqual(sum(r[0].endswith('/execute') for r in self.requests),1)
    def test_restore_validates_and_makes_no_request(self):
        job=self.client.restore(self.body,self.q)
        self.assertEqual(job.quote_id,self.q['quote_id']);self.assertEqual(self.requests,[])
        with self.assertRaises(CyberError):self.client.restore(dict(self.body,prompt='changed'),self.q)
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

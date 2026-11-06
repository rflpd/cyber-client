"""Portable house API consistency contract; no execution or acceptance authority.

Pinned protocol copy from the server sources at commit 47703afb. Public model
metadata describes trial configurations, not advertised or qualified inventory.
It has no server dependencies, filesystem access, provider client or transport.
"""
import copy
import hashlib
import json
import re


SCHEMA = 'cyber-house-trial/1'

MAX_PROMPT = 8192

MAX_OUTPUT = 1536

CONTEXT = 4096

RUNTIME_SHA256 = '519d520f501fa7f096847dd8e28f8bcde9caa400732ae3cd7184efbdbfbb4192'

RUNTIME_ARCHIVE_SHA256 = '600059b6d20ece89e19a20c4e9ab8d23c54cc33e9132a692dffbcc10ed226003'

PROFILES = {
    'qwen35-4b-q4km-b12078/1': {
        'model': 'Qwen3.5-4B', 'file': 'Qwen3.5-4B-Q4_K_M.gguf', 'bytes': 2740937888,
        'model_sha256': '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4',
        'template_sha256': '7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67',
        'thinking': False, 'output_parser': 'plain',
        'generation_suffix': '<|im_start|>assistant\n<think>\n\n</think>\n\n'},
    'qwen35-9b-q4km-b12078/1': {
        'model': 'Qwen3.5-9B', 'file': 'Qwen3.5-9B-Q4_K_M.gguf', 'bytes': 5680522464,
        'model_sha256': '03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8',
        'template_sha256': '7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67',
        'thinking': False, 'output_parser': 'plain',
        'generation_suffix': '<|im_start|>assistant\n<think>\n\n</think>\n\n'},
    'glm47-flash-q4km-b12078/1': {
        'model': 'GLM-4.7-Flash', 'file': 'GLM-4.7-Flash-Q4_K_M.gguf', 'bytes': 18312339808,
        'model_sha256': '29837ed2c0fc5f51981adf8ac8083fcf80743c598381f13e9f06cbad0498b174',
        'template_sha256': 'd63ad536c3c81880043e22ec7fd08db42b4d8fb7c89c7138bc562bfa25281375',
        'thinking': False, 'output_parser': 'plain', 'generation_suffix': '<|assistant|></think>'},
    'deepseek-r1-0528-qwen3-8b-q4km-b12078/1': {
        'model': 'DeepSeek-R1-0528-Qwen3-8B', 'file': 'DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf', 'bytes': 5027785216,
        'model_sha256': 'a86349a4180c4e6bb43f874c29c404fa2be3f90b15509bd6d86f697dba724ec1',
        'template_sha256': '97ee3167ef705d0e9e5dc1610dd019322c60b51dd3823ad51eb5562092e320bc',
        'thinking': True, 'output_parser': 'optional-leading-think', 'generation_suffix': '<｜Assistant｜>'},
}

class ProfileUnavailable(Exception):
    pass

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def profile(identifier):
    if not isinstance(identifier, str) or identifier not in PROFILES:
        raise ProfileUnavailable('house_profile_unavailable')
    return dict(copy.deepcopy(PROFILES[identifier]), id=identifier, runtime='llama.cpp-b12078-cpu',
        runtime_sha256=RUNTIME_SHA256, runtime_archive_sha256=RUNTIME_ARCHIVE_SHA256,
        context_tokens=CONTEXT, max_prompt_bytes=MAX_PROMPT, max_output_tokens=MAX_OUTPUT, max_output_bytes=8192,
        threads=8, slots=1, temperature=0, seed=42, cache_prompt=False,
        prompt_protocol='single-user-template/1')

def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None

def request_record(body):
    fields = {'schema', 'request_id', 'profile_id', 'task_contract_sha256', 'resource_contract_sha256', 'prompt', 'max_output_tokens'}
    if (not isinstance(body, dict) or set(body) != fields or body['schema'] != SCHEMA
            or not isinstance(body['request_id'], str) or not 1 <= len(body['request_id']) <= 100
            or not _hash(body['task_contract_sha256'])
            or not _hash(body['resource_contract_sha256'])
            or not isinstance(body['prompt'], str) or not 1 <= len(body['prompt'].encode()) <= MAX_PROMPT
            or type(body['max_output_tokens']) is not int or not 1 <= body['max_output_tokens'] <= MAX_OUTPUT):
        raise ProfileUnavailable('invalid_house_trial_request')
    selected = profile(body['profile_id'])
    return dict(schema=SCHEMA, request_id=body['request_id'], model=selected['id'],
        prompt=body['prompt'], max_output_tokens=body['max_output_tokens'],
        task_contract_sha256=body['task_contract_sha256'], profile=selected, profile_sha256=digest(selected),
        resource_contract_sha256=body['resource_contract_sha256'],
        prompt_sha256=hashlib.sha256(body['prompt'].encode()).hexdigest())

API_SCHEMA = 'cyber-house-private-api/1'

class HouseClientMismatch(Exception):
    def __init__(self):
        super().__init__('house_client_binding_mismatch')

_QUOTE_FIELDS = frozenset({'quote_id', 'request_id', 'model', 'max_output_tokens',
    'expires_at', 'state', 'request_sha256', 'charged_microusd', 'settlement',
    'semantic_acceptance', 'request_schema', 'profile_sha256',
    'task_contract_sha256', 'prompt_sha256', 'resource_contract_sha256'})

_RESULT_FIELDS = frozenset({'quote_id', 'state', 'model', 'output', 'usage',
    'charged_microusd', 'settlement', 'semantic_acceptance', 'request_schema',
    'profile_sha256', 'task_contract_sha256', 'prompt_sha256',
    'resource_contract_sha256'})

_BOUND_FIELDS = ('request_schema', 'profile_sha256', 'task_contract_sha256',
    'prompt_sha256', 'resource_contract_sha256')

def _record(saved_request):
    try:
        return request_record(saved_request)
    except (ProfileUnavailable, TypeError, ValueError, UnicodeError):
        raise HouseClientMismatch() from None

def _envelope(response):
    if (not isinstance(response, dict) or set(response) != {'api_schema', 'record'}
            or response['api_schema'] != API_SCHEMA or not isinstance(response['record'], dict)):
        raise HouseClientMismatch()
    return response['record']

def _zero(record):
    if (type(record.get('charged_microusd')) is not int or record['charged_microusd'] != 0
            or record.get('settlement') != 'disabled'
            or record.get('semantic_acceptance') is not False):
        raise HouseClientMismatch()

def _hex(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None

def validate_quote(saved_request, response):
    """Bind a private API quote to the caller's exact, normalized house request."""
    request = _record(saved_request)
    quote = _envelope(response)
    if (set(quote) not in (_QUOTE_FIELDS, _QUOTE_FIELDS | {'outcome'})
            or not _hex(quote.get('quote_id'), 32)
            or quote.get('request_id') != request['request_id']
            or quote.get('model') != request['model']
            or type(quote.get('max_output_tokens')) is not int
            or quote['max_output_tokens'] != request['max_output_tokens']
            or type(quote.get('expires_at')) is not int or quote['expires_at'] <= 0
            or quote.get('state') not in ('quoted', 'running', 'completed', 'interrupted')
            or ('outcome' in quote and (quote['state'], quote['outcome']) not in (
                ('completed', 'completed'), ('interrupted', 'outcome_unknown')))
            or quote.get('request_sha256') != hashlib.sha256(json.dumps(request).encode()).hexdigest()):
        raise HouseClientMismatch()
    expected = {'request_schema': request['schema'],
        **{key: request[key] for key in _BOUND_FIELDS if key != 'request_schema'}}
    if any(quote.get(key) != value for key, value in expected.items()):
        raise HouseClientMismatch()
    _zero(quote)
    return dict(quote)

def validate_result(saved_request, saved_quote, response):
    """Validate one completed generation; the caller still owns task acceptance."""
    try:
        return _validate_result(saved_request, saved_quote, response)
    except UnicodeError:
        raise HouseClientMismatch() from None

def _validate_result(saved_request, saved_quote, response):
    request = _record(saved_request)
    quote = validate_quote(saved_request, {'api_schema': API_SCHEMA, 'record': saved_quote})
    result = _envelope(response)
    if (set(result) != _RESULT_FIELDS or result.get('state') != 'completed'
            or result.get('quote_id') != quote['quote_id']
            or result.get('model') != request['model']
            or quote['state'] == 'interrupted'):
        raise HouseClientMismatch()
    if any(result.get(key) != quote[key] for key in _BOUND_FIELDS):
        raise HouseClientMismatch()
    _zero(result)
    output = result.get('output'); usage = result.get('usage')
    if (not isinstance(output, str) or not 1 <= len(output.encode()) <= request['profile']['max_output_bytes']
            or not output.strip() or '<think>' in output or '</think>' in output
            or not isinstance(usage, dict) or usage.get('state') != 'completed'
            or usage.get('generation_complete') is not True
            or usage.get('model_executed') is not True
            or usage.get('cleanup_verified') is not True
            or usage.get('synthetic_response_complete') is not False
            or usage.get('semantic_acceptance') is not False
            or type(usage.get('provider_calls')) is not int or usage['provider_calls'] != 0
            or usage.get('profile_id') != request['model']
            or usage.get('profile_sha256') != request['profile_sha256']
            or usage.get('actual_model') != request['profile']['model']
            or usage.get('model_sha256') != request['profile']['model_sha256']
            or usage.get('runtime_sha256') != request['profile']['runtime_sha256']
            or usage.get('template_sha256') != request['profile']['template_sha256']
            or usage.get('task_contract_sha256') != request['task_contract_sha256']
            or usage.get('resource_contract_sha256') != request['resource_contract_sha256']
            or type(usage.get('prompt_bytes')) is not int
            or usage['prompt_bytes'] != len(request['prompt'].encode())
            or type(usage.get('max_output_tokens')) is not int
            or usage['max_output_tokens'] != request['max_output_tokens']
            or usage.get('stop_type') != 'eos'
            or type(usage.get('reasoning_filtered')) is not bool
            or (request['profile']['output_parser'] == 'plain' and usage['reasoning_filtered'] is not False)
            or type(usage.get('tokens_predicted')) is not int
            or not 1 <= usage['tokens_predicted'] <= request['max_output_tokens']
            or type(usage.get('tokens_evaluated')) is not int
            or not 1 <= usage['tokens_evaluated'] <= request['profile']['context_tokens'] - request['max_output_tokens']
            or not _hex(usage.get('rendered_prompt_sha256'), 64)):
        raise HouseClientMismatch()
    return {'quote_id': quote['quote_id'], 'request_id': request['request_id'],
        'profile_id': request['model'], 'state': 'completed',
        'generation_complete': True, 'semantic_acceptance': False,
        'charged_microusd': 0, 'settlement': 'disabled',
        'output': output, 'output_sha256': hashlib.sha256(output.encode()).hexdigest()}

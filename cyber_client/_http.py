"""Shared bounded JSON transport. No redirects, proxies, retries or raw errors."""
import http.client
import json
import socket
import ssl
import threading
import time

from . import CyberError


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


def post_json(p, path, payload, token, *, timeout, response_timeout=None,
              prefix, max_response, success_statuses=(200, 202)):
    """Make one request to an already validated endpoint with a fixed route.

    Callers validate the endpoint, payload, credentials and response bindings.
    DNS remains subject to the system resolver rather than a process deadline.
    """
    connection = (http.client.HTTPSConnection(p.hostname, p.port, timeout=timeout,
        context=ssl.create_default_context()) if p.scheme == 'https' else
        http.client.HTTPConnection(p.hostname, p.port, timeout=timeout))
    started = time.monotonic()
    response_timer = None
    try:
        connection.connect()
        sock = connection.sock
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise CyberError(prefix + '_transport_outcome_unknown')
        sock.settimeout(remaining)
        connection.request('POST', path, body=payload,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'Connection': 'close', 'Authorization': 'Bearer ' + token})
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise CyberError(prefix + '_transport_outcome_unknown')
        # Execute may legitimately wait for a 300s job plus cleanup. Other
        # operations keep the original short whole-request budget.
        response_budget = response_timeout if response_timeout is not None else remaining
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
            raise CyberError(prefix + '_transport_outcome_unknown') from None
        response = connection.getresponse()
        with response:
            if 300 <= response.status < 400:
                raise CyberError('redirect_refused', response.status)
            if response.status not in success_statuses:
                code = {401: 'unauthorized', 403: 'unauthorized', 409: prefix + '_review_required',
                        429: 'quota_exhausted'}.get(response.status, prefix + '_http_error')
                raise CyberError(code, response.status)
            if (response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json'
                    or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
                raise CyberError('invalid_' + prefix + '_response')
            length = response.getheader('Content-Length')
            transfer = response.getheader('Transfer-Encoding')
            if transfer is not None and (transfer.lower() != 'chunked' or length is not None):
                raise CyberError('invalid_' + prefix + '_response')
            if length is not None and (not length.isdecimal() or len(length) > 10 or int(length) > max_response):
                raise CyberError('response_size_limit')
            raw = bytearray()
            while True:
                remaining = response_budget - (time.monotonic() - started)
                if remaining <= 0:
                    raise CyberError(prefix + '_transport_outcome_unknown')
                sock.settimeout(remaining)
                block = response.read1(min(4096, max_response + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > max_response:
                    raise CyberError('response_size_limit')
                if length is not None and len(raw) == int(length):
                    break
            if length is not None and len(raw) != int(length):
                raise CyberError('invalid_' + prefix + '_response')
            if time.monotonic() - started >= response_budget:
                raise CyberError(prefix + '_transport_outcome_unknown')
            value = _decode(raw)
            if not isinstance(value, dict):
                raise CyberError('invalid_' + prefix + '_response')
            return value
    except (OSError, TimeoutError, http.client.HTTPException):
        raise CyberError(prefix + '_transport_outcome_unknown') from None
    except (ValueError, UnicodeError, RecursionError):
        raise CyberError('invalid_' + prefix + '_response') from None
    finally:
        if response_timer is not None:
            response_timer.cancel()
            if response_timer.ident is not None:
                response_timer.join()
        connection.close()

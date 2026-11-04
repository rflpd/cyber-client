import argparse
import json
import sys
from . import Client, CyberError, DEFAULT_BASE_URL, read_token

def main():
    parser = argparse.ArgumentParser(description='Discover and use Cyber invite-only inference.')
    parser.add_argument('--base', default=DEFAULT_BASE_URL)
    parser.add_argument('--token-file', help='Private invitation token file; never put its contents in command arguments.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('models')
    for name in ('quote', 'run'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--request-id', required=True)
        cmd.add_argument('--model', choices=('small', 'large'), required=True)
        cmd.add_argument('--prompt', required=True)
        cmd.add_argument('--max-output-tokens', type=int, default=128)
    for name in ('execute', 'status'):
        cmd = sub.add_parser(name); cmd.add_argument('--quote-id', required=True)
    args = parser.parse_args()
    try:
        token = read_token(args.token_file) if args.token_file and args.command != 'models' else None
        client = Client(args.base, token)
        if args.command == 'models': result = client.models()
        elif args.command in ('quote', 'run'):
            result = getattr(client, args.command)(request_id=args.request_id, model=args.model,
                prompt=args.prompt, max_output_tokens=args.max_output_tokens)
        else: result = getattr(client, args.command)(args.quote_id)
        print(json.dumps(result, indent=2)); return 0
    except CyberError as error:
        print(json.dumps({'error': error.code, 'http_status': error.status}), file=sys.stderr); return 1

if __name__ == '__main__':
    sys.exit(main())

"""Run from repo root: python3 -m examples.job_flow --token-file /private/invite.token"""
import argparse
import json
from cyber_client import Client, read_token

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--token-file', required=True)
parser.add_argument('--request-id', default='explicit-example-001')
args = parser.parse_args()
client = Client(token=read_token(args.token_file))
quote = client.quote(request_id=args.request_id, model='small',
                     prompt='Reply with exactly HELLO.', max_output_tokens=16)
print(json.dumps(quote, indent=2))
if quote['state'] == 'quoted':
    print(json.dumps(client.execute(quote['quote_id']), indent=2))
print(json.dumps(client.status(quote['quote_id']), indent=2))
# Call status again later if running; do not create another request automatically.

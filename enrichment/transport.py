"""Streaming HTTP with reusable, worker-owned connections."""
from urllib.error import HTTPError

import requests


class Response:
    def __init__(self, response):
        self.response = response
        self.status = response.status_code
        self.headers = response.headers
        self.url = response.url

    def read(self, size=-1):
        return self.response.raw.read(size, decode_content=False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()


def urlopen(request, *, timeout, session):
    response = session.get(request.full_url, headers=dict(request.header_items()),
                           timeout=timeout, stream=True)
    if response.status_code >= 400:
        headers = response.headers
        response.close()
        raise HTTPError(request.full_url, response.status_code, 'HTTP request failed', headers, None)
    return Response(response)


def session():
    client = requests.Session()
    client.headers['Accept-Encoding'] = 'identity'
    # Retry and byte accounting belong to the shared coordinator, not the adapter.
    client.mount('https://', requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=1, max_retries=0))
    return client

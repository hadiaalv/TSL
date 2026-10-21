"""Temporary health WSGI — use only to test hosting. Replace passenger load with this if needed."""

def application(environ, start_response):
    body = b"COS host OK"
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]

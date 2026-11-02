"""WSGI entry point for production servers:  gunicorn -w 1 --threads 4 wsgi:app

One worker on purpose: the models (~1 GB) live in process memory and the
rate limiter / nonce lock are per-process. Threads give request concurrency.
"""
from server import app  # noqa: F401

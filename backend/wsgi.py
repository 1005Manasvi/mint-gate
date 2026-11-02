"""WSGI entry point for production servers:  gunicorn -w 4 --threads 4 wsgi:app

The API is stateless (reports in Postgres, queue + rate limits in Redis, models only in the
workers), so any number of gunicorn workers or containers can serve it.
"""

from server import app  # noqa: F401

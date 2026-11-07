"""WSGI entry for cPanel / Passenger / gunicorn."""

from portal import create_app

application = create_app()
app = application

"""AWS Lambda adapter for the real FastAPI application."""

from mangum import Mangum

from backend.aws_api.app import app


handler = Mangum(app, lifespan="off")

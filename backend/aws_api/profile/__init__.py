"""Authenticated Cognito profile endpoints."""

from .router import ProfileClient, create_profile_router

__all__ = ["ProfileClient", "create_profile_router"]

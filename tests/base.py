import unittest

from app import security


class BaseTestCase(unittest.TestCase):
    """Clears the in-memory rate-limit cache before every test."""
    def setUp(self):
        super().setUp()
        security.clear_rate_limit_cache()

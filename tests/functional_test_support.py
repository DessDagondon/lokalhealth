import unittest

import sqlcipher3
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import app, db


class IsolatedDatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with app.app_context():
            cls.original_engine = db.engines[None]
            cls.test_engine = create_engine(
                'sqlite://',
                creator=lambda: sqlcipher3.connect(':memory:', check_same_thread=False),
                poolclass=StaticPool,
            )
            db.session.remove()
            db.engines[None] = cls.test_engine

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engines[None] = cls.original_engine
        cls.test_engine.dispose()

    def setUp(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with app.app_context():
            db.session.remove()

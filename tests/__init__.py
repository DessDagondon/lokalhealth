import sqlcipher3
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import app, db


_test_engine = create_engine(
    'sqlite://',
    creator=lambda: sqlcipher3.connect(':memory:', check_same_thread=False),
    poolclass=StaticPool,
)

with app.app_context():
    db.session.remove()
    db.engines[None] = _test_engine

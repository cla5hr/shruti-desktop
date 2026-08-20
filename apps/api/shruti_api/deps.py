from collections.abc import Iterator

from sqlalchemy.orm import Session

from shruti_core.db import new_session


def get_db() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()

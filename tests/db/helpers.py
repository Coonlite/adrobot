from sqlalchemy import Engine
from sqlalchemy.orm import Session

from adrobot.db.campaigns import NewStream, add_created_stream, save_campaign
from adrobot.db.models import Stream
from adrobot.db.views import StreamView, stream_view


def seed_campaign(
    engine: Engine, keitaro_id: int, name: str, streams: tuple[NewStream, ...]
) -> int:
    """A stored campaign with its streams, the way Create leaves it. Returns the local id."""
    local_id = save_campaign(engine, keitaro_id, name)
    for new in streams:
        add_created_stream(engine, local_id, new)
    return local_id


def load_stream(engine: Engine, stream_id: int) -> StreamView | None:
    with Session(engine) as session:
        stream = session.get(Stream, stream_id)
        return None if stream is None else stream_view(stream)

import copy
from dataclasses import dataclass

from adrobot.db.models import Stream


@dataclass(frozen=True, slots=True)
class OfferView:
    keitaro_offer_id: int
    share: int
    pinned: bool
    status: str
    unpushed: bool
    published_share: int

    @property
    def id(self) -> int:
        return self.keitaro_offer_id


@dataclass(frozen=True, slots=True)
class StreamView:
    id: int
    campaign_id: int
    keitaro_id: int
    status: str
    unpushed: bool
    offers: tuple[OfferView, ...]
    document: dict[str, object]


def stream_view(stream: Stream) -> StreamView:
    offers = tuple(
        OfferView(
            keitaro_offer_id=row.offer.keitaro_id,
            share=row.share,
            pinned=row.pinned,
            status=row.status,
            unpushed=row.unpushed,
            published_share=row.published_share,
        )
        for row in stream.offers
    )
    return StreamView(
        id=stream.id,
        campaign_id=stream.campaign_id,
        keitaro_id=stream.keitaro_id,
        status=stream.status,
        unpushed=stream.unpushed,
        offers=offers,
        document=copy.deepcopy(stream.document),
    )

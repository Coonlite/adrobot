from dataclasses import dataclass

from sqlalchemy import Engine, select

from adrobot.db.engine import write_session
from adrobot.db.models import Campaign, Offer, OfferBinding, Stream


@dataclass(frozen=True, slots=True)
class NewStream:
    keitaro_id: int
    document: dict[str, object]
    offers: tuple[tuple[int, int], ...]  # (Keitaro offer id, share)


def save_campaign(engine: Engine, keitaro_id: int, name: str) -> int:
    """Record a campaign the moment Keitaro accepts it. Streams follow one by one."""
    with write_session(engine) as session, session.begin():
        campaign = Campaign(keitaro_id=keitaro_id, name=name)
        session.add(campaign)
        session.flush()
        return campaign.id


def add_created_stream(engine: Engine, campaign_id: int, new: NewStream) -> None:
    """Record one stream Keitaro has already accepted."""
    with write_session(engine) as session, session.begin():
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise LookupError(f"campaign {campaign_id} is not stored")
        stream = Stream(campaign=campaign, keitaro_id=new.keitaro_id, document=new.document)
        session.add(stream)
        for offer_keitaro_id, share in new.offers:
            offer = session.scalar(select(Offer).where(Offer.keitaro_id == offer_keitaro_id))
            if offer is None:
                offer = Offer(keitaro_id=offer_keitaro_id)
                session.add(offer)
            session.add(
                OfferBinding(
                    stream=stream,
                    offer=offer,
                    share=share,
                    status="synced",
                    published_share=share,
                )
            )

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str]
    streams: Mapped[list["Stream"]] = relationship(back_populates="campaign", order_by="Stream.id")


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(unique=True)


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    keitaro_id: Mapped[int] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(default="synced")
    unpushed: Mapped[bool] = mapped_column(default=False)
    push_state: Mapped[str] = mapped_column(default="idle")
    push_token: Mapped[str | None] = mapped_column(default=None)
    push_started_at: Mapped[float | None] = mapped_column(default=None)
    document: Mapped[dict[str, object]] = mapped_column(JSON)
    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list["OfferBinding"]] = relationship(back_populates="stream")


class OfferBinding(Base):
    __tablename__ = "offer_bindings"
    __table_args__ = (UniqueConstraint("stream_id", "offer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"))
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    share: Mapped[int]
    pinned: Mapped[bool] = mapped_column(default=False)
    published_pinned: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str]
    unpushed: Mapped[bool] = mapped_column(default=False)
    published_share: Mapped[int] = mapped_column(default=0)
    published_status: Mapped[str] = mapped_column(default="synced")
    stream: Mapped[Stream] = relationship(back_populates="offers")
    offer: Mapped[Offer] = relationship()

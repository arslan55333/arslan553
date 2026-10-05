"""Mini CRM: lead pipeline statuses, notes, and the never-contact-twice guard."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from leadengine.db.models import Business, LeadEvent, LeadStatus, utcnow
from leadengine.errors import LeadEngineError

STATUSES = ["New", "Preview Built", "Emailed", "Replied", "Won", "Lost"]
CONTACTED = {"Emailed", "Replied", "Won", "Lost"}


class AlreadyContacted(LeadEngineError):
    """Another record of the same business (same domain, email or phone) was already contacted."""


def current_status(session: Session, business_id: int) -> str:
    row = session.get(LeadStatus, business_id)
    return row.status if row else "New"


def find_contacted_twin(session: Session, biz: Business) -> Business | None:
    """A *different* business row that shares domain / email / phone and was already contacted."""
    conds = []
    if biz.domain:
        conds.append(Business.domain == biz.domain)
    if biz.best_email:
        conds.append(Business.best_email == biz.best_email)
    if biz.phone_norm:
        conds.append(Business.phone_norm == biz.phone_norm)
    if not conds:
        return None
    stmt = (select(Business).join(LeadStatus, LeadStatus.business_id == Business.id)
            .where(or_(*conds), Business.id != biz.id, LeadStatus.status.in_(CONTACTED)))
    return session.scalar(stmt)


def set_status(session: Session, business_id: int, status: str, note: str | None = None, *,
               force: bool = False) -> LeadStatus:
    if status not in STATUSES:
        raise LeadEngineError(f"Unknown status {status!r}. Use one of: {', '.join(STATUSES)}")
    biz = session.get(Business, business_id)
    if biz is None:
        raise LeadEngineError(f"No business with id {business_id}")
    row = session.get(LeadStatus, business_id)
    previous = row.status if row else "New"
    if status == "Emailed" and not force:
        if previous in CONTACTED:
            raise AlreadyContacted(f"{biz.name} was already contacted (status: {previous})")
        twin = find_contacted_twin(session, biz)
        if twin is not None:
            raise AlreadyContacted(f"{biz.name} looks like {twin.name} (same website/email/phone), "
                                   "which was already contacted")
    if row is None:
        row = LeadStatus(business_id=business_id, status=status)
        session.add(row)
    row.status = status
    row.updated_at = utcnow()
    if status == "Emailed" and row.contacted_at is None:
        row.contacted_at = utcnow()
    if note:
        row.notes = ((row.notes + "\n") if row.notes else "") + note
    session.add(LeadEvent(business_id=business_id, status=status if status != previous else None, note=note))
    if status in CONTACTED and biz.lead_label != "Skip":
        biz.lead_label = "Skip"
        biz.lead_reason = f"skip: already contacted ({status})"
    session.flush()
    return row


def add_note(session: Session, business_id: int, note: str) -> None:
    row = session.get(LeadStatus, business_id) or LeadStatus(business_id=business_id, status="New")
    row.notes = ((row.notes + "\n") if row.notes else "") + note
    session.add(row)
    session.add(LeadEvent(business_id=business_id, note=note))
    session.flush()


def timeline(session: Session, business_id: int) -> list[LeadEvent]:
    return list(session.scalars(select(LeadEvent).where(LeadEvent.business_id == business_id)
                                .order_by(LeadEvent.at.desc(), LeadEvent.id.desc())))

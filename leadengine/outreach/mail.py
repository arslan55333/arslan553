"""Approve -> (opt-in) send, with every safety check in one place.

* Drafts are never sent on their own. ``approve`` turns one draft (+ its follow-ups) into outbox rows.
* ``Sender`` only runs when ``[outreach.sending] enabled = true``; it refuses anything that fails
  ``check_ready`` (address + unsubscribe line, allowed from-domain, suppression list, never-contact-twice,
  daily cap) and waits ``min_delay_seconds`` (+ jitter) between emails.
* ``check_replies`` reads your inbox over IMAP: a reply stops the follow-ups, "unsubscribe" adds the
  address to the suppression list, bounces mark the address invalid.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import random
import re
import smtplib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from leadengine import crm
from leadengine.config import Settings
from leadengine.db.models import Business, OutboundEmail, Suppression, utcnow
from leadengine.db.repo import Repository
from leadengine.errors import LeadEngineError
from leadengine.log import get_logger
from leadengine.outreach.compose import ADDRESS_MISSING, final_body, followup_subject

log = get_logger("outreach")
UNSUB_WORDS = re.compile(r"\b(unsubscribe|remove me|stop emailing|take me off|opt[- ]?out|do not (contact|email))\b|^\s*stop\s*$",
                         re.I | re.M)


class OutreachError(LeadEngineError):
    pass


def outreach_cfg(settings: Settings) -> dict[str, Any]:
    cfg = dict(settings.section("outreach"))
    cfg.setdefault("agency", "")
    if not cfg["agency"]:
        cfg["agency"] = settings.section("preview").get("brand_name", "")
    return cfg


# ── suppression list ─────────────────────────────────────────────────
def suppress(session: Session, value: str, reason: str) -> bool:
    value = value.strip().lower()
    if not value or session.scalar(select(Suppression).where(Suppression.value == value)):
        return False
    session.add(Suppression(value=value, reason=reason))
    session.flush()
    return True


def is_suppressed(session: Session, address: str | None) -> str | None:
    if not address:
        return None
    address = address.strip().lower()
    domain = address.rsplit("@", 1)[-1]
    row = session.scalar(select(Suppression).where(Suppression.value.in_([address, domain])))
    return row.reason or "suppressed" if row else None


# ── approval ─────────────────────────────────────────────────────────
def approve(session: Session, business_id: int, angle: str, *, to: str | None = None, followups: bool = True,
            force: bool = False) -> list[OutboundEmail]:
    """Your explicit OK for one draft (+ follow-ups). Nothing is sent here."""
    biz = session.get(Business, business_id)
    if biz is None:
        raise OutreachError(f"No business with id {business_id}")
    enr = Repository(session).latest_enrichment(business_id, "outreach", fresh_only=False)
    if not enr or not enr.payload:
        raise OutreachError(f"No drafts for {biz.name} yet - run `draft {business_id}` first")
    drafts = enr.payload
    variant = next((v for v in drafts.get("variants") or [] if v.get("angle") == angle), None)
    if variant is None:
        raise OutreachError(f"No '{angle}' draft for {biz.name}")
    to = (to or drafts.get("to") or biz.best_email or "").strip()
    if not to or "@" not in to:
        raise OutreachError(f"{biz.name} has no email address")
    if (biz.email_status == "invalid" and to == biz.best_email) and not force:
        raise OutreachError(f"{to} failed verification")
    if reason := is_suppressed(session, to):
        raise OutreachError(f"{to} is on the do-not-contact list ({reason})")
    _guard_first_contact(session, biz, force)
    existing = session.scalars(select(OutboundEmail).where(OutboundEmail.business_id == business_id,
                                                           OutboundEmail.status.in_(("approved", "scheduled"))))
    for row in existing:                       # re-approving replaces anything not sent yet
        row.status = "cancelled"
    rows = [OutboundEmail(business_id=business_id, to_email=to, step=0, angle=angle, subject=variant["subject"],
                          body=variant["body"], delay_days=0, status="approved", attachment=drafts.get("attachment"))]
    if followups:
        for i, fu in enumerate(drafts.get("followups") or [], start=1):
            rows.append(OutboundEmail(business_id=business_id, to_email=to, step=i, angle=angle,
                                      subject=fu.get("subject") or followup_subject(variant["subject"]),
                                      body=fu["body"], delay_days=int(fu.get("day") or 3 * i), status="scheduled"))
    session.add_all(rows)
    crm.add_note(session, business_id, f"approved '{angle}' email to {to}" + (" + follow-ups" if followups else ""))
    session.flush()
    return rows


def _guard_first_contact(session: Session, biz: Business, force: bool) -> None:
    if force:
        return
    status = crm.current_status(session, biz.id)
    if status in crm.CONTACTED:
        raise crm.AlreadyContacted(f"{biz.name} was already contacted (status: {status})")
    twin = crm.find_contacted_twin(session, biz)
    if twin is not None:
        raise crm.AlreadyContacted(f"{biz.name} looks like {twin.name}, which was already contacted")


# ── message building ─────────────────────────────────────────────────
def build_message(row: OutboundEmail, cfg: dict[str, Any], *, preview_url: str | None, thread_id: str | None = None,
                  attach: bool = False, draft: bool = False) -> EmailMessage:
    sender = (cfg.get("sender_email") or "").strip()
    msg = EmailMessage()
    msg["From"] = f"{cfg.get('sender_name') or cfg.get('agency') or ''} <{sender}>".strip() if sender else ""
    msg["To"] = row.to_email
    msg["Subject"] = row.subject
    msg["Date"] = formatdate(localtime=True)
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else None
    msg["Message-ID"] = make_msgid(domain=domain)
    if cfg.get("reply_to"):
        msg["Reply-To"] = cfg["reply_to"]
    unsub_to = cfg.get("reply_to") or sender
    if unsub_to:
        msg["List-Unsubscribe"] = f"<mailto:{unsub_to}?subject=unsubscribe>"
    if thread_id:
        msg["In-Reply-To"] = thread_id
        msg["References"] = thread_id
    if draft:
        msg["X-Unsent"] = "1"            # Outlook / Thunderbird open it as an editable draft
    msg.set_content(final_body(row.body, cfg, preview_url=preview_url))
    if attach and row.attachment and Path(row.attachment).is_file():
        data = Path(row.attachment).read_bytes()
        subtype = "png" if data[:4] == b"\x89PNG" else "jpeg"
        msg.add_attachment(data, maintype="image", subtype=subtype,
                           filename="concept-preview." + ("png" if subtype == "png" else "jpg"))
    return msg


# ── readiness / compliance checks ────────────────────────────────────
def compliance_problems(cfg: dict[str, Any]) -> list[str]:
    send = cfg.get("sending") or {}
    problems = []
    if not send.get("enabled"):
        problems.append("sending is off ([outreach.sending] enabled = false) - drafts only")
    if not (cfg.get("physical_address") or "").strip():
        problems.append("physical_address is empty in [outreach] (CAN-SPAM needs your postal address)")
    if not (cfg.get("unsubscribe_line") or "").strip():
        problems.append("unsubscribe_line is empty in [outreach]")
    sender = (cfg.get("sender_email") or "").strip().lower()
    if "@" not in sender:
        problems.append("sender_email is empty in [outreach]")
    allowed = [d.lower().lstrip("@") for d in send.get("allowed_from_domains") or []]
    if allowed and "@" in sender and sender.rsplit("@", 1)[1] not in allowed:
        problems.append(f"sender_email domain is not in allowed_from_domains {allowed}")
    if not send.get("smtp_host"):
        problems.append("smtp_host is empty in [outreach.sending]")
    return problems


def sent_last_24h(session: Session, now: datetime | None = None) -> int:
    since = (now or utcnow()) - timedelta(hours=24)
    return session.scalar(select(func.count()).select_from(OutboundEmail)
                          .where(OutboundEmail.status == "sent", OutboundEmail.sent_at >= since)) or 0


def due_rows(session: Session, now: datetime | None = None) -> list[OutboundEmail]:
    now = now or utcnow()
    rows = session.scalars(select(OutboundEmail).where(
        (OutboundEmail.status == "approved") |
        ((OutboundEmail.status == "scheduled") & (OutboundEmail.scheduled_for <= now)))
        .order_by(OutboundEmail.step, OutboundEmail.approved_at))
    return list(rows)


def row_problem(session: Session, row: OutboundEmail) -> str | None:
    """Why this exact email must not go out now (None = OK)."""
    biz = session.get(Business, row.business_id)
    if biz is None:
        return "business deleted"
    if reason := is_suppressed(session, row.to_email):
        return f"{row.to_email} is on the do-not-contact list ({reason})"
    if biz.best_email == row.to_email and biz.email_status == "invalid":
        return f"{row.to_email} is marked invalid"
    if ADDRESS_MISSING in row.body:
        return "body contains the address placeholder"
    status = crm.current_status(session, biz.id)
    if row.step == 0:
        if status in crm.CONTACTED:
            return f"already contacted (status: {status})"
        twin = crm.find_contacted_twin(session, biz)
        if twin is not None:
            return f"same business as {twin.name}, already contacted"
    elif status != "Emailed":
        return f"lead is now '{status}' - follow-ups stop"
    return None


SendFn = Callable[[EmailMessage], None]


def smtp_sender(cfg: dict[str, Any], user: str, password: str) -> SendFn:
    send = cfg.get("sending") or {}
    host, port = send.get("smtp_host"), int(send.get("smtp_port", 587))

    def _send(msg: EmailMessage) -> None:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=60) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as s:
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
    return _send


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    stopped: str | None = None


class Sender:
    def __init__(self, settings: Settings, sf: sessionmaker[Session], *, send_fn: SendFn | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.settings = settings
        self.sf = sf
        self.cfg = outreach_cfg(settings)
        self.send_cfg = self.cfg.get("sending") or {}
        keys = settings.outreach_keys
        self.send_fn = send_fn or smtp_sender(self.cfg, keys.get("smtp_user", ""), keys.get("smtp_password", ""))
        self.sleep = sleep

    async def run(self, *, limit: int | None = None, on_progress: Callable[[str], None] | None = None) -> SendReport:
        say = on_progress or (lambda _m: None)
        report = SendReport()
        problems = compliance_problems(self.cfg)
        if problems:
            report.stopped = "; ".join(problems)
            return report
        max_day = int(self.send_cfg.get("max_per_day", 30))
        delay = float(self.send_cfg.get("min_delay_seconds", 120))
        first = True
        with self.sf() as session:
            for row in due_rows(session):
                budget = max_day - sent_last_24h(session)
                if budget <= 0 or (limit is not None and len(report.sent) >= limit):
                    report.stopped = f"daily limit reached ({max_day}/24h)" if budget <= 0 else "limit reached"
                    break
                if problem := row_problem(session, row):
                    row.status, row.error = "cancelled", problem
                    report.skipped.append(f"{row.to_email} step {row.step}: {problem}")
                    if row.step == 0:
                        for later in session.scalars(select(OutboundEmail).where(
                                OutboundEmail.business_id == row.business_id, OutboundEmail.status == "scheduled")):
                            later.status, later.error = "cancelled", "first email not sent"
                    session.commit()
                    continue
                if not first and delay > 0:
                    await self.sleep(delay + random.uniform(0, delay / 2))
                first = False
                thread = None
                if row.step > 0:
                    head = session.scalar(select(OutboundEmail).where(
                        OutboundEmail.business_id == row.business_id, OutboundEmail.step == 0,
                        OutboundEmail.status == "sent").order_by(OutboundEmail.sent_at.desc()))
                    thread = head.message_id if head else None
                prev = Repository(session).latest_enrichment(row.business_id, "preview", fresh_only=False)
                url = (prev.payload or {}).get("url") if prev else None
                attach = row.step == 0 and (bool(self.send_cfg.get("attach_screenshot")) or "attached" in row.body.lower())
                msg = build_message(row, self.cfg, preview_url=url, thread_id=thread, attach=attach)
                try:
                    await asyncio.to_thread(self.send_fn, msg)
                except Exception as exc:          # SMTP errors: record and move on
                    row.status, row.error = "failed", f"{type(exc).__name__}: {exc}"[:300]
                    report.failed.append(f"{row.to_email}: {row.error}")
                    session.commit()
                    say(f"  failed {row.to_email}: {row.error}")
                    continue
                row.status, row.sent_at, row.message_id, row.error = "sent", utcnow(), msg["Message-ID"], None
                if row.step == 0:
                    crm.set_status(session, row.business_id, "Emailed", f"sent '{row.angle}' email: {row.subject}",
                                   force=True)
                    for later in session.scalars(select(OutboundEmail).where(
                            OutboundEmail.business_id == row.business_id, OutboundEmail.status == "scheduled")):
                        later.scheduled_for = row.sent_at + timedelta(days=later.delay_days or 3)
                else:
                    crm.add_note(session, row.business_id, f"sent follow-up {row.step}")
                session.commit()
                report.sent.append(f"{row.to_email} step {row.step}")
                say(f"  sent {row.to_email} (step {row.step})")
        return report


# ── IMAP: save drafts into your mailbox, read replies ────────────────
def imap_connect(cfg: dict[str, Any], settings: Settings, factory: Callable[..., Any] | None = None):
    host = cfg.get("imap_host")
    keys = settings.outreach_keys
    if not host or not keys.get("smtp_user") or not keys.get("smtp_password"):
        raise OutreachError("set imap_host in [outreach] and OUTREACH_SMTP_USER / OUTREACH_SMTP_PASSWORD in .env")
    conn = (factory or imaplib.IMAP4_SSL)(host)
    conn.login(keys["smtp_user"], keys["smtp_password"])
    return conn


def push_imap_drafts(conn, messages: list[EmailMessage], folder: str) -> int:
    """APPEND each message to your Drafts folder - you review and press send yourself."""
    count = 0
    for msg in messages:
        typ, _ = conn.append(folder, r"(\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise OutreachError(f"IMAP refused the draft ({typ}) - check imap_drafts_folder")
        count += 1
    return count


def _text_of(raw: bytes) -> tuple[str, str]:
    m = email.message_from_bytes(raw)
    sender = parseaddr(m.get("From", ""))[1].lower()
    parts = []
    for part in m.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            try:
                parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
            except Exception:
                continue
    return sender, (m.get("Subject", "") + "\n" + "\n".join(parts))


def check_replies(session: Session, conn, *, days: int = 30) -> dict[str, list[str]]:
    """Mark replies / unsubscribes / bounces from contacted leads. Returns what changed."""
    since = (utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
    out: dict[str, list[str]] = {"replied": [], "unsubscribed": [], "bounced": []}
    sent = list(session.scalars(select(OutboundEmail).where(OutboundEmail.status == "sent", OutboundEmail.step == 0)))
    by_addr = {r.to_email.lower(): r for r in sent}
    conn.select("INBOX", readonly=True)

    def fetch(criteria: str) -> list[bytes]:
        typ, data = conn.search(None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        msgs = []
        for num in data[0].split()[-50:]:
            typ, parts = conn.fetch(num, "(BODY.PEEK[])")
            if typ == "OK" and parts and isinstance(parts[0], tuple):
                msgs.append(parts[0][1])
        return msgs

    def stop_followups(business_id: int, why: str) -> None:
        for row in session.scalars(select(OutboundEmail).where(OutboundEmail.business_id == business_id,
                                                               OutboundEmail.status.in_(("scheduled", "approved")))):
            row.status, row.error = "cancelled", why

    for addr, row in by_addr.items():
        status = crm.current_status(session, row.business_id)
        if status != "Emailed":
            continue
        for raw in fetch(f'(FROM "{addr}" SINCE {since})'):
            _, text = _text_of(raw)
            if UNSUB_WORDS.search(text):
                suppress(session, addr, "asked to unsubscribe")
                crm.set_status(session, row.business_id, "Lost", "asked to unsubscribe")
                out["unsubscribed"].append(addr)
            else:
                crm.set_status(session, row.business_id, "Replied", "reply received")
                out["replied"].append(addr)
            stop_followups(row.business_id, "lead replied")
            break
    for raw in fetch(f'(FROM "mailer-daemon" SINCE {since})') + fetch(f'(FROM "postmaster" SINCE {since})'):
        _, text = _text_of(raw)
        low = text.lower()
        for addr, row in by_addr.items():
            if addr in low and addr not in out["bounced"]:
                suppress(session, addr, "bounced")
                biz = session.get(Business, row.business_id)
                if biz is not None and biz.best_email and biz.best_email.lower() == addr:
                    biz.email_status = "invalid"
                crm.add_note(session, row.business_id, f"email bounced: {addr}")
                stop_followups(row.business_id, "bounced")
                out["bounced"].append(addr)
    session.flush()
    return out

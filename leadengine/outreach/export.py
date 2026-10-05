"""Get drafts out of LeadEngine without sending anything:

* ``to_merge_csv`` - one row per lead, ready to import into Instantly / Smartlead / lemlist / GMass
  (custom columns for each follow-up), or any mail-merge tool.
* ``write_eml`` - one ``.eml`` per lead (opens as an editable draft in Outlook / Thunderbird / Apple Mail).
* ``push_webhook`` - POST the drafts as JSON to n8n / Make / Zapier.
IMAP "save to my Drafts folder" lives in ``mail.push_imap_drafts``.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadengine.db.models import Business, Enrichment, OutboundEmail
from leadengine.db.repo import Repository
from leadengine.http import HttpClient
from leadengine.outreach.compose import final_body, followup_subject
from leadengine.outreach.mail import build_message


def load_draft_items(session: Session, business_ids: list[int] | None = None
                     ) -> tuple[list[tuple[Business, dict[str, Any]]], list[str]]:
    """(business, drafts) for leads that have drafts and may still be contacted; plus skipped names."""
    from leadengine import crm
    from leadengine.outreach.mail import is_suppressed

    if business_ids is None:
        business_ids = list(dict.fromkeys(session.scalars(
            select(Enrichment.business_id).where(Enrichment.kind == "outreach").order_by(Enrichment.id))))
    repo = Repository(session)
    items, skipped = [], []
    for bid in dict.fromkeys(business_ids):
        biz = repo.get_business(bid)
        enr = repo.latest_enrichment(bid, "outreach", fresh_only=False) if biz else None
        if not biz or not enr or not enr.payload:
            continue
        if crm.current_status(session, bid) in crm.CONTACTED or is_suppressed(session, enr.payload.get("to")):
            skipped.append(biz.name)
            continue
        items.append((biz, enr.payload))
    return items, skipped


def pick_variant(drafts: dict[str, Any], angle: str) -> dict[str, Any] | None:
    variants = drafts.get("variants") or []
    if angle == "best":
        facts = drafts.get("facts") or {}
        angle = "competitor" if facts.get("competitor") else "short"
    return next((v for v in variants if v.get("angle") == angle), variants[0] if variants else None)


def merge_rows(items: list[tuple[Business, dict[str, Any]]], cfg: dict[str, Any], angle: str) -> list[dict[str, Any]]:
    rows = []
    for biz, drafts in items:
        v = pick_variant(drafts, angle)
        if v is None:
            continue
        url = drafts.get("preview_url")
        facts = drafts.get("facts") or {}
        row = {"email": drafts.get("to") or biz.best_email or "", "first_name": facts.get("first_name") or "",
               "company_name": biz.name, "city": biz.city or "", "phone": biz.phone or "", "website": biz.website or "",
               "preview_url": url or "", "angle": v["angle"], "subject": v["subject"],
               "body": final_body(v["body"], cfg, preview_url=url)}
        for i, fu in enumerate(drafts.get("followups") or [], start=1):
            row[f"followup_{i}_day"] = fu.get("day")
            row[f"followup_{i}_subject"] = fu.get("subject") or followup_subject(v["subject"])
            row[f"followup_{i}_body"] = final_body(fu["body"], cfg, preview_url=url)
        rows.append(row)
    return rows


def to_merge_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    fields = list(dict.fromkeys(k for r in rows for k in r))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    return "﻿" + buf.getvalue()


def draft_messages(items: list[tuple[Business, dict[str, Any]]], cfg: dict[str, Any], angle: str, *,
                   attach: bool = True):
    """(business, EmailMessage) for each lead - first email only, marked as an unsent draft."""
    for biz, drafts in items:
        v = pick_variant(drafts, angle)
        if v is None:
            continue
        row = OutboundEmail(business_id=biz.id, to_email=drafts.get("to") or biz.best_email or "", step=0,
                            angle=v["angle"], subject=v["subject"], body=v["body"], attachment=drafts.get("attachment"))
        yield biz, build_message(row, cfg, preview_url=drafts.get("preview_url"),
                                 attach=attach and "attached" in v["body"].lower(), draft=True)


def write_eml(items: list[tuple[Business, dict[str, Any]]], cfg: dict[str, Any], angle: str, folder: Path) -> list[Path]:
    from leadengine.preview.builder import slugify

    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for biz, msg in draft_messages(items, cfg, angle):
        path = folder / f"{biz.id}-{slugify(biz.name)}.eml"
        path.write_bytes(msg.as_bytes())
        out.append(path)
    return out


async def push_webhook(http: HttpClient, url: str, items: list[tuple[Business, dict[str, Any]]],
                       cfg: dict[str, Any], angle: str) -> int:
    rows = merge_rows(items, cfg, angle)
    for row in rows:
        r = await http.request("POST", url, json={"type": "leadengine.draft", **row}, retries=1)
        r.raise_for_status()
    return len(rows)

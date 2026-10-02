"""Pages and endpoints the agency's client opens from a link. No sign-in: the unguessable
token in the link is the permission. Pages are plain server-rendered HTML with no scripts."""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import change_orders as co_svc, confirmations
from ..db import get_db
from ..schemas import PublicConfirmIn, PublicDecisionIn

router = APIRouter(tags=["client links"])

KIND = {"decision": "Decision", "approval": "Approval", "action": "Action item"}
CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"

STYLE = """
:root{--bg:#F3F5F2;--card:#fff;--ink:#14302B;--ink2:#4C5F5A;--rule:#D5DDD8;--ok:#1F6F5C;--bad:#9B2F45;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0F1A18;--card:#16231F;--ink:#E4ECE8;--ink2:#A9BAB4;--rule:#2A3B36;--ok:#5FC2A3;--bad:#E58A9C}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:680px;margin:0 auto;padding:28px 18px 60px}h1{font-size:26px;line-height:1.2;margin:4px 0 6px}
.sub{color:var(--ink2);margin:0 0 22px}.card{background:var(--card);border:1px solid var(--rule);border-radius:12px;padding:16px 18px;margin-bottom:12px}
.kind{font-size:13px;color:var(--ink2)}.what{font-weight:600;margin:2px 0}.meta{font-size:14px;color:var(--ink2)}
.choice{display:flex;gap:16px;margin-top:10px;flex-wrap:wrap}.choice label{display:flex;gap:6px;align-items:center;cursor:pointer}
textarea,input[type=text]{width:100%;font:inherit;padding:9px 11px;border:1px solid var(--rule);border-radius:8px;background:var(--bg);color:var(--ink);margin-top:8px}
button{font:inherit;font-weight:600;border:0;border-radius:8px;padding:11px 18px;cursor:pointer;background:var(--ok);color:#fff}
button.secondary{background:transparent;color:var(--bad);border:1px solid var(--bad)}
table{width:100%;border-collapse:collapse;font-size:15px}td,th{padding:8px 4px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
td.n,th.n{text-align:right;white-space:nowrap}.total td{font-weight:700;border-bottom:0}
.notice{padding:14px 16px;border-radius:10px;background:var(--card);border:1px solid var(--rule)}.foot{margin-top:28px;font-size:13px;color:var(--ink2)}
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>{escape(title)}</title><style>{STYLE}</style></head>'
            f'<body><main>{body}<p class="foot">Sent through Onrecord.</p></main></body></html>')
    return HTMLResponse(html, status_code=status, headers={"Content-Security-Policy": CSP, "X-Robots-Tag": "noindex, nofollow",
                                                           "X-Frame-Options": "DENY"})


def message(title: str, text: str, status: int = 200) -> HTMLResponse:
    return page(title, f'<h1>{escape(title)}</h1><div class="notice">{escape(text)}</div>', status)


def error_page(e: HTTPException) -> HTMLResponse:
    d = e.detail
    return message("This link can't be used", d if isinstance(d, str) else d.get("message", "Something went wrong."), e.status_code)


# --- confirmation ----------------------------------------------------------------------

def confirm_html(v: dict, token: str, error: str = "") -> HTMLResponse:
    if v["status"] == "completed":
        return message("Thank you", f"{v['responder_name']} already responded to this. {v['agency']} has your answers.")
    if v["status"] == "expired":
        return message("This link has expired", f"Ask {v['agency']} to send a new one.", 410)
    items = []
    for it in v["items"]:
        rid = escape(it["record_id"])
        meta = []
        if it.get("owner"):
            meta.append(f"Who: {escape(it['owner'])}")
        if it.get("due"):
            meta.append(f"By: {escape(str(it['due']))}")
        items.append(
            f'<div class="card"><div class="kind">{escape(KIND.get(it["type"], "Agreement"))}'
            f'{" · " + escape(it["topic"]) if it.get("topic") else ""}</div>'
            f'<div class="what">{escape(it["text"])}</div>'
            f'{"<div class=meta>" + " · ".join(meta) + "</div>" if meta else ""}'
            f'<div class="choice"><label><input type="radio" name="r_{rid}" value="confirmed" required> That\'s right</label>'
            f'<label><input type="radio" name="r_{rid}" value="disputed"> Not quite</label></div>'
            f'<textarea name="c_{rid}" rows="2" maxlength="1000" placeholder="If not quite, what should it say?"></textarea></div>')
    err = f'<div class="notice" role="alert" style="border-color:var(--bad);color:var(--bad);margin-bottom:14px">{escape(error)}</div>' if error else ""
    body = (f'<div class="kind">{escape(v["agency"])} for {escape(v["client"])}</div>'
            f'<h1>Please confirm what we agreed</h1>'
            f'<p class="sub">From our call “{escape(v["call_title"])}” on {escape(v["call_date"])}. '
            f'Mark each item, then submit once. It takes about a minute.</p>{err}'
            f'<form method="post" action="/confirm/{escape(token)}">{"".join(items)}'
            f'<label>Your name<input type="text" name="name" required maxlength="120" autocomplete="name"></label>'
            f'<p style="margin-top:16px"><button type="submit">Submit</button></p></form>')
    return page("Please confirm what we agreed", body)


@router.get("/confirm/{token}", response_class=HTMLResponse, include_in_schema=False)
def confirm_page(token: str, db: Session = Depends(get_db)):
    try:
        return confirm_html(confirmations.public_view(db, confirmations.load_by_token(db, token)), token)
    except HTTPException as e:
        return error_page(e)


@router.post("/confirm/{token}", response_class=HTMLResponse, include_in_schema=False)
async def confirm_submit(token: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    answers = {}
    for k, v in form.items():
        if k.startswith("r_"):
            rid = k[2:]
            answers[rid] = (str(v), str(form.get(f"c_{rid}", "")))
    try:
        res = confirmations.respond(db, token, str(form.get("name", "")), answers)
    except HTTPException as e:
        if e.status_code == 422:
            db.rollback()
            return confirm_html(confirmations.public_view(db, confirmations.load_by_token(db, token)), token,
                                e.detail if isinstance(e.detail, str) else "Please check your answers.")
        return error_page(e)
    text = "Thank you, everything is confirmed." if not res["disputed"] else (
        f"Thank you. You confirmed {res['confirmed']} and flagged {res['disputed']}; we'll follow up on what needs changing.")
    return message("Thank you", text)


@router.get("/v1/public/confirmations/{token}", tags=["client links"])
def confirm_json(token: str, db: Session = Depends(get_db)):
    return confirmations.public_view(db, confirmations.load_by_token(db, token))


@router.post("/v1/public/confirmations/{token}", tags=["client links"])
def confirm_json_submit(token: str, body: PublicConfirmIn, db: Session = Depends(get_db)):
    return confirmations.respond(db, token, body.name, {str(a.record_id): (a.response, a.comment) for a in body.answers})


# --- change order approval ------------------------------------------------------------------

def co_html(v: dict, token: str) -> HTMLResponse:
    if v["status"] in ("approved", "declined"):
        return message(f"Change order {v['status']}", f"{v['decided_by_name']} {v['status']} this change order. {v['agency']} has been told.")
    if v["status"] == "expired":
        return message("This link has expired", f"Ask {v['agency']} to send a new one.", 410)
    rows = "".join(f'<tr><td>{escape(ln["description"])}</td><td class="n">{escape(ln["quantity"])} {escape(ln["unit"])}</td>'
                   f'<td class="n">{escape(ln["unit_price_text"])}</td><td class="n">{escape(ln["amount_text"])}</td></tr>'
                   for ln in v["lines"])
    notes = f'<p class="sub">{escape(v["notes"])}</p>' if v["notes"] else ""
    body = (f'<div class="kind">{escape(v["agency"])} for {escape(v["client"])}</div>'
            f'<h1>Change order #{v["number"]}: {escape(v["title"])}</h1>'
            f'<p class="sub">This is extra work outside our current agreement. Please approve it before we start.</p>{notes}'
            f'<div class="card"><table><thead><tr><th>Item</th><th class="n">Qty</th><th class="n">Rate</th><th class="n">Amount</th></tr></thead>'
            f'<tbody>{rows}<tr><td colspan="3" class="n">Subtotal</td><td class="n">{escape(v["subtotal_text"])}</td></tr>'
            f'<tr><td colspan="3" class="n">Tax ({escape(v["tax_percent"])}%)</td><td class="n">{escape(v["tax_text"])}</td></tr>'
            f'<tr class="total"><td colspan="3" class="n">Total</td><td class="n">{escape(v["total_text"])}</td></tr></tbody></table></div>'
            f'<form method="post" action="/change-order/{escape(token)}">'
            f'<label>Your name<input type="text" name="name" required maxlength="120" autocomplete="name"></label>'
            f'<label>Comment (optional)<textarea name="comment" rows="2" maxlength="1000"></textarea></label>'
            f'<p class="choice"><button type="submit" name="decision" value="approve">Approve {escape(v["total_text"])}</button>'
            f'<button type="submit" name="decision" value="decline" class="secondary">Decline</button></p></form>')
    return page(f"Change order #{v['number']}", body)


@router.get("/change-order/{token}", response_class=HTMLResponse, include_in_schema=False)
def co_page(token: str, db: Session = Depends(get_db)):
    try:
        return co_html(co_svc.public_view(db, co_svc.load_by_token(db, token)), token)
    except HTTPException as e:
        return error_page(e)


@router.post("/change-order/{token}", response_class=HTMLResponse, include_in_schema=False)
async def co_submit(token: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    decision = str(form.get("decision", ""))
    if decision not in ("approve", "decline"):
        return message("Please choose", "Go back and choose Approve or Decline.", 422)
    try:
        co = co_svc.decide(db, token, decision == "approve", str(form.get("name", "")), str(form.get("comment", "")))
    except HTTPException as e:
        return error_page(e)
    return message(f"Change order {co.status}", "Thank you. The agency has been told." if co.status == "approved"
                   else "Thank you. The agency will be in touch.")


@router.get("/v1/public/change-orders/{token}", tags=["client links"])
def co_json(token: str, db: Session = Depends(get_db)):
    return co_svc.public_view(db, co_svc.load_by_token(db, token))


@router.post("/v1/public/change-orders/{token}", tags=["client links"])
def co_json_decide(token: str, body: PublicDecisionIn, db: Session = Depends(get_db)):
    co = co_svc.decide(db, token, body.decision == "approve", body.name, body.comment)
    return {"status": co.status}

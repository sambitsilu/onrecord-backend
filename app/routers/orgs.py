"""Organizations (agencies) and their members."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import OrgAccess, current_user, org_access, require_owner
from ..config import get_settings
from ..db import allow_erasure, get_db
from ..models import Membership, Organization, User
from ..schemas import ChainOut, MemberInvite, MemberOut, OrgCreate, OrgOut

router = APIRouter(prefix="/v1/orgs", tags=["organizations"])


@router.get("", response_model=list[OrgOut])
def list_orgs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Organization, Membership.role).join(Membership, Membership.org_id == Organization.id)
                      .where(Membership.user_id == user.id).order_by(Organization.created_at)).all()
    return [OrgOut(id=o.id, name=o.name, role=role) for o, role in rows]


@router.post("", response_model=OrgOut, status_code=201)
def create_org(body: OrgCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = Organization(name=body.name.strip(), monthly_token_budget=get_settings().default_monthly_token_budget)
    db.add(org)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    audit.append(db, org_id=org.id, actor_user_id=user.id, action="org_created", entity_type="org",
                 entity_id=str(org.id), note=f"Organization “{org.name}” created")
    db.commit()
    return OrgOut(id=org.id, name=org.name, role="owner")


@router.get("/{org_id}/members", response_model=list[MemberOut])
def list_members(access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    rows = db.execute(select(Membership, User.email).outerjoin(User, User.id == Membership.user_id)
                      .where(Membership.org_id == access.org_id).order_by(Membership.created_at)).all()
    return [MemberOut(id=m.id, email=email or m.invited_email or "", role=m.role, pending=m.user_id is None) for m, email in rows]


@router.post("/{org_id}/members", response_model=MemberOut, status_code=201)
def invite_member(body: MemberInvite, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    require_owner(access)
    email = body.email.lower()
    existing = db.execute(select(Membership).outerjoin(User, User.id == Membership.user_id)
                          .where(Membership.org_id == access.org_id,
                                 (func.lower(User.email) == email) | (func.lower(Membership.invited_email) == email))).first()
    if existing:
        raise HTTPException(409, "That person is already a member or invited.")
    m = Membership(org_id=access.org_id, invited_email=email, role=body.role)
    db.add(m)
    db.flush()
    audit.append(db, org_id=access.org_id, actor_user_id=access.user.id, action="member_invited", entity_type="membership",
                 entity_id=str(m.id), note=f"Invited {email} as {body.role}")
    db.commit()
    return MemberOut(id=m.id, email=email, role=m.role, pending=True)


@router.delete("/{org_id}/members/{member_id}", status_code=204)
def remove_member(member_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    require_owner(access)
    m = db.execute(select(Membership).where(Membership.id == member_id, Membership.org_id == access.org_id)).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "Member not found.")
    if m.role == "owner":
        owners = db.execute(select(func.count()).select_from(Membership).where(
            Membership.org_id == access.org_id, Membership.role == "owner", Membership.user_id.is_not(None))).scalar_one()
        if owners <= 1 and m.user_id is not None:
            raise HTTPException(409, "An organization needs at least one owner.")
    audit.append(db, org_id=access.org_id, actor_user_id=access.user.id, action="member_removed", entity_type="membership",
                 entity_id=str(m.id), note="Removed a member")
    db.delete(m)
    db.commit()
    return Response(status_code=204)


@router.get("/{org_id}/audit/verify", response_model=ChainOut)
def verify_audit_chain(access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return audit.verify_chain(db, access.org_id)


@router.delete("/{org_id}", status_code=204)
def delete_org(access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Permanently delete the organization and all its data (owner only)."""
    require_owner(access)
    allow_erasure(db)
    db.execute(Organization.__table__.delete().where(Organization.id == access.org_id))
    db.commit()
    return Response(status_code=204)

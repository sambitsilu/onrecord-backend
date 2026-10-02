"""Authentication (Google Identity Platform / Firebase ID tokens) and org access."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Client, Membership, User


@dataclass
class Identity:
    uid: str
    email: str
    email_verified: bool
    name: str


def _verify_firebase(token: str) -> Identity:
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    s = get_settings()
    try:
        claims = id_token.verify_firebase_token(token, g_requests.Request(), audience=s.firebase_project_id)
    except Exception as e:  # noqa: BLE001 - any verification failure is a 401
        raise HTTPException(401, "Your sign-in has expired. Sign in again.") from e
    if not claims or claims.get("iss") != f"https://securetoken.google.com/{s.firebase_project_id}":
        raise HTTPException(401, "Your sign-in isn't valid for this app.")
    return Identity(uid=claims["sub"], email=(claims.get("email") or "").lower(),
                    email_verified=bool(claims.get("email_verified")), name=claims.get("name") or "")


def _verify_dev(token: str) -> Identity:
    if not token.startswith("dev:") or "@" not in token:
        raise HTTPException(401, "In dev mode, send 'Authorization: Bearer dev:<email>'.")
    email = token[4:].strip().lower()
    return Identity(uid="dev:" + email, email=email, email_verified=True, name=email.split("@")[0])


_bearer = HTTPBearer(auto_error=False, description="Google Identity Platform ID token (or dev:<email> in local mode)")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Sign in to continue.")
    token = creds.credentials.strip()
    s = get_settings()
    ident = _verify_dev(token) if s.auth_mode == "dev" else _verify_firebase(token)
    user = db.execute(select(User).where(User.auth_uid == ident.uid)).scalar_one_or_none()
    if user is None:
        user = User(auth_uid=ident.uid, email=ident.email, name=ident.name)
        db.add(user)
        db.flush()
    # Accept pending invitations addressed to this verified email.
    if ident.email and ident.email_verified:
        invites = db.execute(select(Membership).where(Membership.user_id.is_(None),
                                                      func.lower(Membership.invited_email) == ident.email)).scalars().all()
        for inv in invites:
            already = db.execute(select(Membership.id).where(Membership.org_id == inv.org_id,
                                                             Membership.user_id == user.id)).first()
            if already:
                db.delete(inv)
            else:
                inv.user_id = user.id
    db.commit()
    return user


@dataclass
class OrgAccess:
    user: User
    org_id: uuid.UUID
    role: str

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def org_access(org_id: uuid.UUID = Path(...), user: User = Depends(current_user), db: Session = Depends(get_db)) -> OrgAccess:
    m = db.execute(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user.id)).scalar_one_or_none()
    if m is None:
        # 404, not 403: don't reveal that the organization exists.
        raise HTTPException(404, "Not found.")
    return OrgAccess(user=user, org_id=org_id, role=m.role)


def require_owner(access: OrgAccess) -> None:
    if not access.is_owner:
        raise HTTPException(403, "Only an owner of this organization can do that.")


def get_client(db: Session, access: OrgAccess, client_id: uuid.UUID) -> Client:
    c = db.execute(select(Client).where(Client.id == client_id, Client.org_id == access.org_id)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Client not found.")
    return c

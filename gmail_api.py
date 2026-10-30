"""A small Gmail API client for the help desk mailbox, using only the standard library.

Access comes from a one-time Google sign-in as the help desk mailbox (OAuth for a desktop
app, with PKCE). The refresh token it gives only works for that one mailbox, and is saved in
gmail.json next to the database (never in settings.json or backups).
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Read, label and send mail in this one mailbox (no deleting).
SCOPE = "https://www.googleapis.com/auth/gmail.modify"


class GmailError(Exception):
    """A problem talking to Google, worded for the person setting it up."""


# ---------------------------------------------------------------- stored secrets


def secrets_path(data_dir):
    return os.path.join(data_dir, "gmail.json")


def load_secrets(data_dir):
    try:
        with open(secrets_path(data_dir)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_secrets(data_dir, data):
    path = secrets_path(data_dir)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_client_file(text):
    """The OAuth client ID and secret from the JSON file Google Cloud lets you download."""
    try:
        data = json.loads(text)
    except ValueError:
        raise GmailError("That isn't the JSON file from Google Cloud (it couldn't be read).")
    client = data.get("installed") or data.get("web") or {}
    if not client.get("client_id") or not client.get("client_secret"):
        raise GmailError("That file doesn't contain an OAuth client ID and secret. Download it from "
                         "Google Cloud → APIs & Services → Credentials → your Desktop app client.")
    if "installed" not in data:
        raise GmailError("That client is a \"Web application\". Create one of type \"Desktop app\" instead.")
    return {"client_id": client["client_id"], "client_secret": client["client_secret"]}


# ---------------------------------------------------------------- HTTP


def _request(url, data=None, headers=None, method=None):
    body = None
    headers = dict(headers or {})
    if isinstance(data, dict) and headers.get("Content-Type") == "application/json":
        body = json.dumps(data).encode()
    elif isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
        except ValueError:
            detail = {}
        error = detail.get("error")
        message = (error.get("message") if isinstance(error, dict) else detail.get("error_description")) \
            or (error if isinstance(error, str) else "") or e.reason
        if e.code in (401, 400) and (error == "invalid_grant" or "invalid_grant" in str(detail)):
            raise GmailError("Google no longer accepts the saved sign-in (it was revoked, or the "
                             "mailbox's password changed). Click Connect mailbox again.")
        if e.code == 403 and "has not been used" in str(message):
            raise GmailError("The Gmail API isn't turned on in your Google Cloud project. Turn it on "
                             "(APIs & Services → Library → Gmail API), wait a minute, and try again.")
        raise GmailError(f"Google returned an error ({e.code}): {message}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise GmailError(f"Couldn't reach Google ({getattr(e, 'reason', e)}). Check the internet connection.")


# ---------------------------------------------------------------- sign-in (OAuth)


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id, redirect_uri, state, challenge, mailbox=None):
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if mailbox:
        params["login_hint"] = mailbox
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(client, code, redirect_uri, verifier):
    tokens = _request(TOKEN_URL, {
        "client_id": client["client_id"], "client_secret": client["client_secret"], "code": code,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    })
    if not tokens.get("refresh_token"):
        raise GmailError("Google didn't return a lasting sign-in. Remove the app's access at "
                         "myaccount.google.com/permissions for the mailbox, then connect again.")
    return tokens


# ---------------------------------------------------------------- the mailbox


class GmailClient:
    """The help desk mailbox. Every call refreshes the access token as needed."""

    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self._token, self._expires = None, 0
        self._labels = {}

    @classmethod
    def from_secrets(cls, data):
        if not data.get("refresh_token"):
            raise GmailError("The help desk mailbox isn't connected yet.")
        return cls(data["client_id"], data["client_secret"], data["refresh_token"])

    def _access_token(self):
        if not self._token or time.time() > self._expires - 60:
            tokens = _request(TOKEN_URL, {
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
            self._token = tokens["access_token"]
            self._expires = time.time() + int(tokens.get("expires_in", 3600))
        return self._token

    def _call(self, path, data=None, method=None, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return _request(url, data, headers, method)

    def address(self):
        return self._call("/profile")["emailAddress"]

    def label_id(self, name):
        """The id of a Gmail label, creating it the first time."""
        if name not in self._labels:
            for label in self._call("/labels").get("labels", []):
                self._labels[label["name"]] = label["id"]
        if name not in self._labels:
            created = self._call("/labels", {"name": name, "labelListVisibility": "labelShow",
                                             "messageListVisibility": "show"})
            self._labels[name] = created["id"]
        return self._labels[name]

    def list_ids(self, query, limit=50):
        """Message ids matching a Gmail search, oldest first."""
        found = self._call("/messages", params={"q": query, "maxResults": limit}).get("messages", [])
        return [m["id"] for m in reversed(found)]

    def get_raw(self, message_id):
        """(raw RFC 822 bytes, Gmail thread id) of a message."""
        msg = self._call(f"/messages/{message_id}", params={"format": "raw"})
        return base64.urlsafe_b64decode(msg["raw"] + "=" * (-len(msg["raw"]) % 4)), msg.get("threadId")

    def add_label(self, message_id, label_id):
        self._call(f"/messages/{message_id}/modify", {"addLabelIds": [label_id]})

    def send(self, raw_bytes, thread_id=None):
        """Send a message; returns (Gmail message id, thread id)."""
        body = {"raw": base64.urlsafe_b64encode(raw_bytes).decode()}
        if thread_id:
            body["threadId"] = thread_id
        sent = self._call("/messages/send", body)
        return sent.get("id"), sent.get("threadId")

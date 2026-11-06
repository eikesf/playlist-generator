"""`apple-music-mcp login`: store and validate the music.apple.com session token."""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from ..apple.errors import AppleMusicError
from ..config import CredentialStore
from ..token_expiry import parse_expiry, record_user_token_expiry
from . import ContainerBuilder, Output, default_container

MIN_TOKEN_LENGTH = 20

TOKEN_HELP = """\
How to get your media-user-token (one time, about 2 minutes):

  Safari: Settings > Advanced > tick "Show features for web developers".
          Open https://music.apple.com and sign in.
          Develop menu > Show Web Inspector > Storage tab > Cookies > music.apple.com
          Copy the value of the cookie named  media-user-token

  Chrome: open https://music.apple.com and sign in, press Cmd+Option+I,
          Application tab > Cookies > https://music.apple.com > media-user-token

The token stays on this Mac (macOS Keychain) and is only sent to Apple.
"""

EXPIRY_HELP = """Optional: the cookie's expiry date, so you get a reminder before it stops working.
In the same Cookies list, the "Expires" column of media-user-token shows it (for example 2027-05-04).
"""


async def run_login(
    token: str | None,
    storefront: str | None,
    use_browser: bool,
    *,
    build: ContainerBuilder = default_container,
    store: CredentialStore | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
    ask: Callable[[str], str] = input,
    expires: str | None = None,
    out: Output = print,
) -> int:
    store = store or CredentialStore()
    creds = store.load()
    interactive = token is None and sys.stdin.isatty()
    expires_at: datetime | None = None
    if storefront:
        creds.storefront = storefront.lower()
    if use_browser:
        from ..browser_login import capture_tokens
        from ..config import config_dir

        captured = await capture_tokens(config_dir() / "browser-profile", creds.storefront)
        if not captured.media_user_token:
            out(
                "No media-user-token cookie seen. Did the sign-in finish? Retry, or paste the token manually."
            )
            return 1
        creds.media_user_token = captured.media_user_token
        creds.developer_token = captured.developer_token or creds.developer_token
        if captured.media_user_token_expires:
            expires_at = datetime.fromtimestamp(captured.media_user_token_expires, UTC)
    else:
        if not token:
            out(TOKEN_HELP)
            token = prompt("Paste media-user-token (input hidden): ")
        token = (token or "").strip().strip('"')
        if len(token) < MIN_TOKEN_LENGTH:
            out("That does not look like a media-user-token (too short).")
            return 1
        creds.media_user_token = token
        if expires is None and interactive:
            out(EXPIRY_HELP)
            expires = ask("Cookie expiry date (YYYY-MM-DD, Enter to estimate 180 days): ")
        expires_at = parse_expiry(expires or "")

    app = build(creds)
    app.set_persist(False)  # save only after Apple accepted the token
    try:
        await app.tokens.get()
        account_storefront = await app.library.storefront()
    except AppleMusicError as exc:
        out(f"Login check failed: {exc}")
        return 1
    finally:
        await app.aclose()
    if not storefront and account_storefront:
        creds.storefront = account_storefront
    store.save(creds)
    expiry = record_user_token_expiry(app.state, expires_at)
    when = expiry["expires_at"][:10] + (" (estimated)" if expiry["estimated"] else "")
    out(f"OK. Signed in (storefront '{creds.storefront}'). Credentials saved to {store.location}")
    out(f"The session token expires around {when}. You will get a reminder before that.")
    from ..operations import PENDING_KEY

    pending = app.state.get(PENDING_KEY, []) or []
    if pending:
        names = ", ".join(f"'{p['name']}'" for p in pending)
        out(f"{len(pending)} playlist(s) waiting to be created: {names}. Run: apple-music-mcp finish-pending")
    return 0

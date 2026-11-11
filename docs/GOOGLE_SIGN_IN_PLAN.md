# Google sign-in: review and plan

Date: 10 November 2026. Status: phases 1 and 2 are built. Phases 3 and 4 are a plan.

## 1. Summary

**Goal.** An Administrator invites a person who has a Google account. The
person gets an invitation email, signs in with Google, and signs in with
Google each time after that.

**Result of the review.** The app is in a good position for this work.

- The invitation flow is there already. Today it ends with "choose a
  password". The plan adds a second ending: "Continue with Google".
- The app already has a full OAuth 2.0 flow for Xero. The Google flow uses
  the same pattern and the same HTTP library.
- Lockout, two-step sign-in, the audit trail and session limits all hang off
  Django's sign-in signal. A Google sign-in sends the same signal, so these
  controls continue to work.

**Recommendation.** Write a small Google OpenID Connect flow in the `core`
app. Do not add a sign-in library. Section 4 gives the reasons and the
alternative.

**Size.** Approximately 400 lines of application code, 350 lines of tests,
one database migration, and 30 minutes in the Google Cloud console.

**Two faults found in the current invitation flow** (section 3.3). The plan
repairs them, because Google users would hit them more often:

1. An invited user whose link has expired cannot recover by themselves.
2. There is no "send the invitation again" button.

## 2. The tech stack

| Area | What the app uses |
|---|---|
| Framework | Django 5.2 (LTS), Python 3.11 |
| Pages | Django templates, HTMX (`hx-boost` on `<body>`), Alpine.js, Tailwind 4 |
| Users | Django's stock `User` model. The email address is the username. |
| Permissions | `Role` and `UserRole` in `core/models.py`. A user with no role sees nothing. |
| Hosting | Railway (web, worker, Postgres, Redis). Vercel is being retired (`docs/CUTOVER.md`). |
| Email | Django email backend. Resend or Postmark is planned (`docs/GO_LIVE_CHECKLIST.md`, phase 5). |
| Dependencies | `requirements.in` compiled to a hash-locked `requirements.txt`. CI runs `pip-audit`, `bandit` and `gitleaks`. |

## 3. Review of the current sign-in code

### 3.1 The parts and where they are

| Part | File | What it does |
|---|---|---|
| Sign-in page | `templates/registration/login.html` | Django's stock `LoginView`. Stand-alone page, not in the app layout. |
| Password check | `core/auth_backends.py` | `EmailOrUsernameBackend`. Accepts email or old username. |
| Lockout | django-axes, `core/axes_username.py`, `core/account_security.py` | 5 wrong passwords lock the identity for 30 minutes. |
| Two-step sign-in | `core/mfa.py`, `MFAMiddleware` in `core/middleware.py` | TOTP and recovery codes. Mandatory for Administrators. |
| Session limits | `AbsoluteSessionLifetimeMiddleware` | 7 days idle, 30 days absolute. |
| Audit trail | `core/audit.py`, `AuditEvent`, `core/signals.py` | Records sign-in, sign-out and each account change. |
| User management | `core/views/users.py`, `core/forms.py` | Settings → Users. Create, edit, deactivate, unlock, reset two-step. |
| Invitation | `core/views/account.py` | `send_invite`, `InviteTokenGenerator`, `InviteConfirmView`. |
| Xero OAuth | `xero_integration/client.py`, `views.py` | Authorization code flow with `requests`. A model to copy. |

### 3.2 How an invitation works today

1. An Administrator opens Settings → Users → Add user and leaves the password
   empty (`UserCreateForm.sends_invite`).
2. The app creates the user with an **unusable password** and the selected
   role.
3. `send_invite` emails a link: `/accounts/invite/<uid>/<token>/`.
4. The token is signed. It has its own salt, it expires after
   `INVITE_TIMEOUT` (3 days), and it stops working when the password or
   `last_login` changes. Thus it works one time only.
5. The person opens the link, chooses a password, then signs in.
6. The audit trail records `USER_INVITED` and `INVITE_ACCEPTED`.

This is a sound design. Steps 1 to 4 stay as they are. Only step 5 gets a
second option.

### 3.3 Faults in the current invitation flow

**Fault 1: an expired invitation is a dead end.**
The invitation email says: "If it has expired, use Forgot your password?".
The expired-link page says the same. This advice does not work. Django's
`PasswordResetForm.get_users` sends no email to a user who has an unusable
password, and each invited user has one. The person asks for a reset, sees
"email sent", and gets nothing.

**Fault 2: an Administrator cannot send the invitation again.**
`user_update` has no "resend" action. The only way out is for the
Administrator to set a password by hand, which is what the invitation flow
was made to prevent.

A user who signs in only with Google never has a password. Thus both faults
would become permanent for these users. Phase 1 repairs them.

### 3.4 Things that make the Google work easy

- **Signals do the work.** `core/signals.py` listens to `user_logged_in`. It
  starts the session clock, records the audit event and stores the two-step
  status. Django's `login()` sends this signal for any sign-in method.
- **`MFAMiddleware` is independent of the password.** It examines the session
  after sign-in. A Google sign-in goes through it with no change.
- **Cookies are correct for a redirect flow.** `SESSION_COOKIE_SAMESITE` is
  `Lax`. The browser sends the session cookie on the redirect back from
  Google, because it is a top-level GET.
- **HTTPS detection is correct.** `SECURE_PROXY_SSL_HEADER` is set, so the app
  builds `https://` callback addresses behind Railway's proxy.
- **`requests` and `cryptography` are installed already.**

### 3.5 Things that need care

| # | Item | Why it matters | Plan |
|---|---|---|---|
| 1 | Two backends in `AUTHENTICATION_BACKENDS` | `login()` must be told which backend signed the user in. | Add a small `GoogleBackend` and pass it by name. |
| 2 | django-axes | A Google sign-in must not count as a wrong password, and a locked password must not block Google. | Do not call `authenticate()`. Call `login()` directly after the Google checks pass. |
| 3 | Content-Security-Policy `form-action 'self'` | Chrome applies `form-action` to the redirect that follows a form post. The Google button is a form post that redirects to Google. Chrome will block it when `CSP_ENFORCE=True`. | Add `https://accounts.google.com` to `form-action`. |
| 4 | `hx-boost` on `<body>` | HTMX would follow the redirect to Google with a background request. The browser blocks that. | Put `hx-boost="false"` on each Google form inside the app layout. The sign-in page is stand-alone and is not affected. |
| 5 | Vercel preview addresses | Google permits exact redirect addresses only. No wildcards. Each preview has a different host. | Google sign-in is off where the client ID is not set. Do not set it on previews. |
| 6 | The production domain is not bought yet | The redirect address changes when the domain changes. | Add a line to `GO_LIVE_CHECKLIST.md` phase 5.1, next to the Xero redirect line. |
| 7 | `AuditEvent.action` has `max_length=40` | New action names must fit. | New names are less than 40 characters. The new choices need a migration. |
| 8 | Rate-limit counters are per process without `CACHE_URL` | Known gap in `SECURITY.md`. | No change. The Google endpoints get the same limiter as password reset. |

## 4. Library decision

### 4.1 Options

| Option | New packages | New tables | Fit with this app |
|---|---|---|---|
| **A. Small custom flow (recommended)** | 0 | 1 | Copies the Xero pattern. Each line is the app's own. |
| B. django-allauth 65.21 | 3 (`django-allauth`, `oauthlib`, `pyjwt`) | 5 or more | Good library, but made for public sign-up. Needs work to switch that off. |
| C. Authlib 1.9 | 1 or 2 | 1 | Good OAuth client. Does not give the user-linking logic, which is the difficult part. |
| D. Google Identity Services button or One Tap | 0 | 1 | Loads a script from Google on the sign-in page. Needs CSP changes for scripts, frames and connections. |

### 4.2 Why option A

- **The app is invitation-only.** django-allauth creates accounts on first
  sign-in by default (`SOCIALACCOUNT_AUTO_SIGNUP = True`). You must write an
  adapter (`is_open_for_signup` returns `False`), enable
  `EMAIL_AUTHENTICATION` for Google, and enable
  `EMAIL_AUTHENTICATION_AUTO_CONNECT`. Then the important security logic is
  still custom code, but inside the library's hooks.
- **django-allauth needs its account app.** `allauth.account` must be in
  `INSTALLED_APPS` with its middleware, its `EmailAddress` table and its
  authentication backend. Its URLs (`/accounts/login/`, `/accounts/logout/`,
  password reset) collide with the views this app already has, hardened and
  tested.
- **The repository has a clear rule on dependencies.** `requirements.in`
  says: "an unused dependency is attack surface and audit noise". Option A
  adds none.
- **The protocol part is small for one provider.** One redirect, one token
  request, six checks on the reply. The Xero client shows the team can
  maintain this.
- **Option D weakens the CSP.** All scripts are self-hosted today. A
  server-side redirect keeps `script-src 'self'`.

### 4.3 When to select django-allauth instead

Select option B if you plan to add Microsoft, Apple or SAML sign-in in the
next year. One custom flow is small. Three custom flows are a library.

## 5. Design

### 5.1 Principles

1. **No self sign-up.** Google sign-in never creates a user. An Administrator
   creates each user and gives the role.
2. **Identify the Google account by `sub`, not by email.** Google's
   documentation says `sub` is unique and never changes, and that the email
   "may not be unique to this account and could change over time".
3. **The first link needs two proofs.** The person must open the invitation
   link (proof of the mailbox) and sign in to a Google account with the same
   verified email (proof of the Google account).
4. **Keep no Google tokens.** The app reads the ID token one time and
   discards it. It asks for no API access. There is nothing to encrypt or
   leak.
5. **Two-step sign-in rules do not change.** See decision D2.

### 5.2 Data model

One new model in `core/models.py`:

```python
class GoogleIdentity(models.Model):
    """The Google account a user signs in with. One per user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='google_identity',
    )
    # Google's permanent ID for the account. The lookup key at sign-in.
    sub = models.CharField(max_length=255, unique=True)
    # Copies from the ID token, for display and for the audit trail only.
    email = models.EmailField()
    hosted_domain = models.CharField(max_length=255, blank=True)
    linked_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()
```

The `User` model does not change.

### 5.3 Settings

```python
GOOGLE_OAUTH_CLIENT_ID = env('GOOGLE_OAUTH_CLIENT_ID', default='')
GOOGLE_OAUTH_CLIENT_SECRET = env('GOOGLE_OAUTH_CLIENT_SECRET', default='')
GOOGLE_SIGN_IN_ENABLED = bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)
```

With no client ID the feature is off: no button, and the URLs return 404.
This is the same pattern as PostHog. Local development, CI and Vercel
previews need no configuration.

The redirect address comes from the request
(`request.build_absolute_uri(reverse('google_callback'))`). `ALLOWED_HOSTS`
already protects the host name.

### 5.4 URLs

| URL | Method | Purpose |
|---|---|---|
| `/accounts/google/start/` | POST | Makes `state`, `nonce` and the PKCE verifier, then redirects to Google. |
| `/accounts/google/callback/` | GET | Google sends the person back here. |
| `/accounts/google/disconnect/` | POST | A signed-in user removes their link. |

`start` is POST only, with a CSRF token. A GET link would let another site
start a sign-in for the person.

### 5.5 Flow A: accept an invitation with Google (first time)

1. The Administrator adds the user and leaves the password empty. No change.
2. The person opens the invitation link. `InviteConfirmView` checks the token
   and shows the page. The page has two options:
   - **Continue with Google**
   - Choose a password (the form that is there today)
3. The person selects **Continue with Google**. The form posts to
   `/accounts/google/start/` with `mode=invite`.
4. `start` reads the invitation token from the session (Django's
   `_password_reset_token` key) and checks it again. It then stores this in
   the session:

   ```python
   request.session['_google_oauth'] = {
       'state': secrets.token_urlsafe(32),
       'nonce': secrets.token_urlsafe(32),
       'code_verifier': secrets.token_urlsafe(64),
       'mode': 'invite',
       'invite_user_id': user.pk,
       'invite_token': token,
       'next': safe_next,
       'started': int(time.time()),
   }
   ```

5. `start` redirects to
   `https://accounts.google.com/o/oauth2/v2/auth` with:

   | Parameter | Value |
   |---|---|
   | `client_id` | `GOOGLE_OAUTH_CLIENT_ID` |
   | `response_type` | `code` |
   | `scope` | `openid email profile` |
   | `redirect_uri` | the callback address |
   | `state` | from the session |
   | `nonce` | from the session |
   | `code_challenge`, `code_challenge_method` | SHA-256 of the verifier, `S256` |
   | `login_hint` | the invited email address |
   | `prompt` | `select_account` |

   `login_hint` selects the correct Google account for a person who has
   more than one.

6. Google sends the person to the callback with `code` and `state`.
7. `callback` does these checks in sequence. A failure at any step stops the
   flow.

   | # | Check |
   |---|---|
   | 1 | Take `_google_oauth` out of the session (one use only). It is there and less than 10 minutes old. |
   | 2 | `state` is equal to the stored value (`secrets.compare_digest`). |
   | 3 | No `error` parameter. If the person cancelled, show a calm message. |
   | 4 | Exchange the `code` at `https://oauth2.googleapis.com/token` with the client secret and the `code_verifier`. Timeout 10 seconds. |
   | 5 | Decode the ID token. `iss` is `https://accounts.google.com` or `accounts.google.com`. `aud` is the client ID. `exp` is in the future. `nonce` is equal to the stored value. |
   | 6 | `email_verified` is true. |

8. Invite mode then does these checks:

   | # | Check |
   |---|---|
   | 7 | The invitation token is still valid for `invite_user_id`. |
   | 8 | The user is active. |
   | 9 | The Google email is equal to the user's email (case ignored). |
   | 10 | No other user has this `sub`. |

9. The app creates the `GoogleIdentity`, records `GOOGLE_LINKED` and
   `INVITE_ACCEPTED`, and calls `login()`.
10. `login()` updates `last_login`. This makes the invitation token invalid.
    The link cannot be used again.
11. `MFAMiddleware` then applies the usual two-step rules.

**ID token signature.** The plan does not check the signature. OpenID
Connect Core (section 3.1.3.7) permits this when the app receives the token
directly from the token endpoint over TLS. django-allauth does the same for
Google (`_decode_id_token` in its Google provider). The other checks in
step 5 are mandatory.

### 5.6 Flow B: sign in again with Google (each time after)

1. The sign-in page shows **Sign in with Google** above the email and
   password fields.
2. The button posts to `/accounts/google/start/` with `mode=login`.
3. Steps 4 to 7 of flow A, without the invitation data and without
   `login_hint`.
4. `callback` looks for a `GoogleIdentity` with this `sub`.
   - **Found, and the user is active:** update `last_used_at`, call
     `login()`, go to `next` or the dashboard.
   - **Not found, or the user is not active:** show one message for both
     cases: "No Cgate account uses that Google account. Open the link in your
     invitation email, or ask your administrator." Record the refusal.

The lookup is by `sub`. If the person changes their Google email address
later, sign-in continues to work.

### 5.7 Flow C: a current password user connects Google

1. Account security page → **Connect Google**.
2. `start` with `mode=connect`. The user is signed in already.
3. `callback` checks that the Google email is equal to the user's email and
   that `sub` is not in use, then creates the `GoogleIdentity`.
4. Record `GOOGLE_LINKED`.

**Disconnect.** A user can remove their own link only if they have a usable
password. If not, they would lock themselves out. Record `GOOGLE_UNLINKED`.

### 5.8 Administrator controls (Settings → Users → edit user)

| Control | Purpose |
|---|---|
| "Sign-in" row | Shows "Google (name@example.com), last used 3 Nov" or "Password". |
| **Send invitation again** | Shown while the user has not signed in. Sends a new link. Repairs fault 2. |
| **Disconnect Google** | For a lost or changed Google account. Ends the user's sessions, then the Administrator sends a new invitation. |

"Send invitation again" also works for a user who has signed in before but
has no password and no Google link. This is the recovery path that replaces
"Forgot your password?" for these users.

### 5.9 Text changes

| Place | Change |
|---|---|
| `invite_email.txt` | "To finish setting up your account, open this link. You can sign in with Google or choose a password." Remove the "Forgot your password?" advice. Add: "If the link has expired, ask {{ invited_by }} to send a new one." |
| Expired-link page | For invitations, remove the reset link. Tell the person to ask for a new invitation. |
| `user_form.html` | "Leave the password blank to email an invitation. The new user then signs in with Google or chooses a password." |
| `login.html` | Add the Google button and a place for the refusal message. |

### 5.10 The button

Google's branding rules apply. Use the text "Sign in with Google" or
"Continue with Google". Use the standard colour "G" logo, not a
single-colour or redrawn one. Keep the file in `static/img/` so that
`img-src 'self'` covers it.

### 5.11 Code layout

| File | Change |
|---|---|
| `core/google_oidc.py` (new) | Build the authorisation URL, exchange the code, check the ID token. No Django views. Easy to test. |
| `core/views/google.py` (new) | `google_start`, `google_callback`, `google_disconnect`. |
| `core/auth_backends.py` | Add `GoogleBackend`: `authenticate()` returns `None`; `get_user` comes from `ModelBackend`. |
| `core/models.py` | `GoogleIdentity`; new `AuditEvent.Action` values. |
| `core/migrations/00NN_google_identity.py` (new) | The table and the new choices. |
| `core/views/account.py` | Share the invitation token check with the Google views. |
| `core/views/users.py` | `resend_invite` and `google_disconnect` actions. |
| `horse_management/settings.py` | The three settings, the backend, the CSP `form-action` entry. |
| `horse_management/urls.py` | Three paths, before `django.contrib.auth.urls`. |
| Templates | `login.html`, `password_reset_confirm.html`, `account/security.html`, `settings/user_form.html`, `invite_email.txt`. |
| `.env.example`, `SECURITY.md`, `docs/GO_LIVE_CHECKLIST.md`, `docs/DATA_RETENTION.md` | Document the settings, the control, the console steps and the new personal data (`sub`, Google email). |

New audit actions: `GOOGLE_LINKED`, `GOOGLE_UNLINKED`,
`GOOGLE_SIGN_IN_REFUSED`. The `SIGNED_IN` event gets the detail "Google".

## 6. Security review of the design

| Threat | Control |
|---|---|
| A stranger with a Google account signs in | No user is created at sign-in. `sub` must be linked already. |
| Someone makes a Google account on an address they do not own | `email_verified` must be true, and the first link also needs the invitation link from the mailbox. |
| A leaked invitation link | The attacker also needs a Google account with the invited email, verified. Today a leaked link is sufficient to set a password. |
| Forged callback (CSRF on sign-in) | `state` in the session, compared in constant time, used one time. `start` is POST with a CSRF token. |
| Replayed ID token | `nonce` in the session, checked against the token. |
| Stolen authorisation code | PKCE verifier stays in the server session. The client secret is also necessary. |
| Open redirect through `next` | Use the existing `_safe_next` check (`url_has_allowed_host_and_scheme`). |
| Session fixation | `login()` makes a new session key. |
| A deactivated user | `is_active` is checked at each sign-in. "Sign out everywhere" works as it does today. |
| Finding out who has an account | One refusal message for "not linked" and "not active". |
| A person loses the Google account | An Administrator disconnects it and sends a new invitation. |
| Google is down | Password users are not affected. Google-only users wait. Document this as a known limit. |
| The client secret leaks | It is an environment variable on Railway. `gitleaks` scans the repository. Rotate it in the Google console. |
| A reused company domain (old Google Workspace domain bought by another party) | The new party gets a new `sub`. The lookup is by `sub`, so they cannot sign in as the old user. |

## 7. Google Cloud console setup

Approximately 30 minutes. Do it one time for each environment that needs
Google sign-in.

1. Go to the Google Cloud console and create a project, for example
   "Cgate".
2. Open **Google Auth Platform**.
3. **Branding:** app name "Cgate", a support email address, and the app's
   domain as an authorised domain.
4. **Audience:** select **External**. Staff can have any Google account,
   which includes `gmail.com`.
5. **Data access:** add only `openid`, `email` and `profile`. These are not
   sensitive scopes.
6. **Clients:** create an OAuth client of type **Web application**. Add
   these redirect addresses, letter for letter:
   - `https://<production host>/accounts/google/callback/`
   - `http://localhost:8000/accounts/google/callback/`
7. Copy the client ID and the client secret to the Railway **web** service:
   `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
8. **Publish** the app (status "In production").

Notes:

- Google's help page says an app that asks for only name, email and profile
  does not need the test-user list and its authorisations do not expire
  after 7 days. Verification is for sensitive and restricted scopes.
- Redirect addresses must use HTTPS. `localhost` is the exception. No
  wildcards. A wrong address gives the error `redirect_uri_mismatch`.
- When the real domain goes live, add its redirect address here. Do this in
  the same step as the Xero redirect change (`GO_LIVE_CHECKLIST.md` 5.1).

## 8. Work plan

Each phase is one pull request and can go live by itself.

### Phase 0: decisions and console (you, 45 minutes)

- [ ] Answer the decisions in section 9.
- [ ] Do the console setup in section 7 for local development.

### Phase 1: repair the invitation flow (done)

No Google code. Useful with or without the other phases.

- [x] "Send invitation again" on the user edit page, with an audit event.
- [x] Correct the email text and the expired-link page (fault 1).
- [x] Tests: resend makes the old link invalid; the expired page gives the
      correct advice.

### Phase 2: Google sign-in for linked users (done)

- [x] `GoogleIdentity` model and migration.
- [x] `core/google_oidc.py` with unit tests. Use `unittest.mock` for the
      token request, as the Xero tests do.
- [x] `google_start`, `google_callback`, `GoogleBackend`, settings, URLs.
- [x] Button on the sign-in page. CSP `form-action` entry.
- [x] Flow C (connect and disconnect) on the Account security page.
- [x] Audit events.

Built against mocked Google replies (`core/tests/test_google_sign_in.py`).
It is not yet tested against the real Google service. Differences from
the design in section 5:

- `GoogleIdentity` has no `HistoricalRecords`. The link and unlink are
  `AuditEvent` rows, and a history row for each sign-in (`last_used_at`)
  would be noise.
- `GoogleBackend` is a `BaseBackend`, not a `ModelBackend`, so that
  permissions are not answered twice.
- The start of the flow is limited to `GOOGLE_SIGN_IN_LIMIT_PER_IP` (30)
  for each IP address in an hour.
- A person with no linked Google account can only use flow C (connect
  from Account Security) until phase 3 adds the invitation button.

### Phase 3: invitation with Google

- [ ] "Continue with Google" on the invitation page (flow A).
- [ ] Administrator controls: sign-in row and "Disconnect Google".
- [ ] Update `SECURITY.md`, `.env.example`, `GO_LIVE_CHECKLIST.md`,
      `DATA_RETENTION.md`.

### Phase 4: go live

- [ ] Console setup for production. Set the two variables on Railway.
- [ ] Invite yourself on a second Google account. Do the full cycle two
      times: invite, accept with Google, sign out, sign in with Google.
- [ ] Examine the audit log for the events.
- [ ] If `CSP_ENFORCE` is on, do the test in Chrome.

### Test list (phases 2 and 3)

| Area | Cases |
|---|---|
| Feature flag | No client ID: no button, URLs give 404. |
| `start` | GET is refused. Session holds `state`, `nonce`, verifier. Redirect URL has each parameter. |
| `callback`, protocol | No session data. Wrong `state`. Data older than 10 minutes. `error=access_denied`. Token request fails or times out. Wrong `iss`, `aud`, `nonce`. Expired token. `email_verified` false. Second use of the same `state`. |
| Sign-in | Linked `sub` signs in. Not-linked `sub` is refused. Inactive user is refused, same message. Changed Google email still signs in. `next` is honoured only when safe. |
| Invitation | Happy path creates the link and signs in. Email mismatch is refused. Expired token is refused. Link cannot be used twice. `sub` in use by another user is refused. |
| Other controls | Axes lockout on the password does not block Google. An Administrator with no TOTP goes to set-up. A user with TOTP goes to the code page. Audit events are written. Session start time is set. |
| Connect and disconnect | Email must be equal. Disconnect is refused with no usable password. Administrator disconnect ends sessions. |

## 9. Decisions for you

| # | Decision | Recommendation |
|---|---|---|
| D1 | Custom flow or django-allauth? | Custom flow (section 4). Select allauth only if more providers are planned. |
| D2 | Must an Administrator who signs in with Google also give a TOTP code? | Yes, no change in phase 2. The app cannot see if the Google account has 2-Step Verification. You can excuse one user with the existing "Excuse from two-step sign-in" control. |
| D3 | Can a person link Google without the invitation link, on email match only? | No, at the start. It is safer. If people often ignore the email and go directly to the sign-in page, relax it later for `gmail.com` and Google Workspace addresses only. |
| D4 | Add a "Google only" option for a user (no password permitted)? | Not now. Add it later if you want to stop passwords for some staff. |
| D5 | Limit sign-in to one Google Workspace domain? | No. Yard staff have personal Gmail accounts. The invitation controls who gets in. |

## 10. Sources

Read directly in this session:

- django-allauth 65.21.1 source and bundled documentation, from PyPI:
  `allauth/socialaccount/providers/google/`, `app_settings.py`,
  `docs/socialaccount/configuration.rst`, `docs/installation/quickstart.rst`.
- Django 5.2 source: `django/contrib/auth/forms.py`
  (`PasswordResetForm.get_users`) and `views.py`
  (`PasswordResetConfirmView`).
- This repository.

Found through web search. The pages could not be opened in this session,
because `developers.google.com` and `docs.allauth.org` are outside the
session's permitted network. **Read these again before the build starts**:

- [Google: OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)
- [Google: OpenID Connect reference](https://developers.google.com/identity/openid-connect/reference)
  (`sub`, `email`, `email_verified`, `hd`, `nonce`, `login_hint`, `prompt`)
- [Google: Manage app audience](https://support.google.com/cloud/answer/15549945)
  (testing and production status, test users)
- [Google: redirect URI rules](https://support.google.com/cloud/answer/15549257)
- [Google: OAuth production readiness](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)
- [Google: Sign in with Google branding guidelines](https://developers.google.com/identity/branding-guidelines)
- [django-allauth: release notes](https://docs.allauth.org/en/latest/release-notes/recent.html)
- [django-allauth: social account configuration](https://docs.allauth.org/en/latest/socialaccount/configuration.html)
- [OpenID Connect Core 1.0, ID token validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)

Not confirmed from Google's own page: that Google accepts PKCE
(`code_challenge`) for the Web application client type. django-allauth's
documentation shows `OAUTH_PKCE_ENABLED: True` for Google, which indicates
that it does. Confirm this in phase 2. If Google refuses the parameter,
remove it; `state`, `nonce` and the client secret stay.

The Chrome behaviour in section 3.5 item 3 (`form-action` applied to
redirects after a form post) is from general knowledge, not from a source
read in this session. The phase 4 test in Chrome confirms it.

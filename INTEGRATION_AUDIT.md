# Integration Endpoint Audit

## Summary

| File | Endpoint | Method | Auth | Timeout | Error Handling | Status |
|------|----------|--------|------|---------|----------------|--------|
| eternitas/client.py | /api/v1/bots/register | POST | X-API-Key | 10s | ConnectError, HTTPStatusError | **CORRECT** |
| eternitas/client.py | /api/v1/registry/verify/{id} | GET | None (public) | 10s | ConnectError -> None | **CORRECT** |
| eternitas/client.py | /api/v1/lookup | GET | None | 10s | ConnectError -> None | **CORRECT** |
| eternitas/client.py | /api/v1/admin/revoke/{id} | POST | Bearer (admin) | 10s | ConnectError -> error result | **CORRECT** |
| eternitas/client.py | /api/v1/passport/{id}/services | PATCH | Bearer (admin) | 10s | **NONE** | **BUG** |
| tools/windy_api.py | /api/v1/user/history | GET | Bearer (JWT) | 10s | ConnectError, HTTPError | **CORRECT** |
| tools/windy_api.py | /api/v1/recordings/list | GET | Bearer (JWT) | 10s | ConnectError, HTTPError | **CORRECT** |
| tools/windy_api.py | /api/v1/clone/training-data | GET | Bearer (JWT) | 10s | ConnectError, HTTPStatusError (404) | **CORRECT** |
| tools/windy_api.py | /api/v1/translate/text | POST | Bearer (JWT) | 10s | ConnectError, HTTPError | **CORRECT** |
| mail_provision.py | /api/v1/provision/bot | POST | X-Service-Token | 10s | ConnectError, Exception | **CORRECT** |
| channels/email.py | /api/v1/send (Windy Mail) | POST | Bearer (jmap_token) | 10s | Exception | **CORRECT** |
| channels/email.py | /api/v1/inbox | GET | Bearer (jmap_token) | 30s | Exception | **CORRECT** |
| channels/email.py | sendgrid.com/v3/mail/send | POST | Bearer (API key) | **NONE** | Exception | **BUG** |
| channels/sms.py | Twilio Messages.json | POST | Basic (SID:token) | **NONE** | Exception | **BUG** |
| phone_provision.py | Twilio AvailablePhoneNumbers | GET | Basic (SID:token) | 15s | Exception -> mock | **CORRECT** |
| phone_provision.py | Twilio IncomingPhoneNumbers | POST | Basic (SID:token) | 15s | Exception -> mock | **CORRECT** |
| matrix_provision.py | /_synapse/admin/v1/register | GET+POST | HMAC in body | 10/15s | Exception -> None | **CORRECT** |
| matrix_provision.py | /_matrix/client/v3/login | POST | Password in body | 10s | `except Exception: pass` | **WARN** |
| windy_cloud.py | /api/storage/files/upload | POST | Bearer (JWT) | 60s | ConnectError, Exception | **CORRECT** |
| windy_cloud.py | /api/storage/health | GET | Bearer (JWT) | 10s | ConnectError, Exception | **CORRECT** |
| windy_clone.py | /api/v1/clone/training-data | GET | Bearer (JWT) | 10s | Exception | **CORRECT** |
| windy_traveler.py | /api/v1/translate/text | POST | Bearer (JWT) | 15s | Exception | **CORRECT** |
| windy_word.py | /api/v1/recordings/list | GET | Bearer (JWT) | 10s | Exception | **CORRECT** |
| windy_word.py | /api/v1/recordings/{id} | GET | Bearer (JWT) | 10s | Exception | **CORRECT** |
| contact_discovery.py | /api/v1/discover | POST | None | 10s | Exception | **UNKNOWN** |
| push_gateway.py | /api/v1/push | POST | None | 10s | Exception | **UNKNOWN** |

## Bugs Requiring Fixes

### 1. `eternitas/client.py` — `update_services()` has NO error handling
Every other method in EternitasClient wraps calls in try/except. This one will crash the caller on any network failure.

### 2. `channels/email.py` — SendGrid path has no timeout
`WindyFlyEmail.send_email()` uses `urllib.request.urlopen` with no timeout. Can hang indefinitely.

### 3. `channels/sms.py` — Twilio SMS has no timeout
`send_sms()` uses `urllib.request.urlopen` with no timeout. Can hang indefinitely.

### 4. `matrix_provision.py` — Silent exception swallowing
`_login_existing_bot()` has `except Exception: pass` with zero logging. Debugging blind spot.

### 5. `tools/windy_api.py` — No trailing slash protection
`_get_api_url()` does not `.rstrip("/")`. If WINDY_API_URL has a trailing slash, URLs will have `//api/...`.

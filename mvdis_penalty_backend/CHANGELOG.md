# Changelog

## 0.2.1

- Work around the current TWCA root compatibility issue by disabling only
  Python's strict X.509 flag for MVDIS connections.
- Keep certificate-authority, hostname, validity-period, and signature
  verification enabled; insecure TLS is not used.

## 0.2.0

- Convert the project to one Home Assistant add-on; HACS is no longer required.
- Publish five Home Assistant entities directly through the internal Core API.
- Fire `mvdis_penalty_new_case` and create a persistent notification for new records.
- Remove the loopback companion API and unnecessary host networking.

## 0.1.1

- Add the local integration brand icon required by HACS validation.
- Add the repository topics required by HACS validation.

## 0.1.0

- Initial experimental release for Home Assistant OS `amd64`.
- Local MVDIS CAPTCHA OCR and scheduled query.
- Loopback-only JSON API for the companion HACS integration.

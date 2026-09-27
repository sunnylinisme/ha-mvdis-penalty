# Taiwan MVDIS Penalty for Home Assistant

Experimental Home Assistant package for checking a user's own unpaid traffic
penalties on Taiwan's official MVDIS website and notifying Home Assistant when a
new record appears.

> [!WARNING]
> This is an unofficial community project. It is not affiliated with or endorsed
> by the Ministry of Transportation and Communications or the Highway Bureau.
> The government website can change without notice and break the query parser.
> Always verify important information on the official website.

## Why the project has two components

Home Assistant OS runs Core in Alpine Linux. The local OCR runtime required for
the MVDIS image CAPTCHA is distributed for glibc Linux, not Alpine/musl. A
single pure HACS integration therefore cannot install the OCR reliably.

This repository provides:

1. **Taiwan MVDIS Penalty Backend add-on** — a Debian-based, `amd64` add-on that
   performs the government-site query and CAPTCHA OCR locally.
2. **Taiwan MVDIS Penalty HACS integration** — exposes entities, the manual
   refresh button, new-record detection, events, and notifications.

The backend listens only on `127.0.0.1:8099`. Identity data and CAPTCHA images
remain inside the Home Assistant host and are not sent to an external OCR API.

## Compatibility

The first release targets Home Assistant OS on `amd64`, including a Synology
DS723+ running Home Assistant OS in Virtual Machine Manager.

## Installation

Use the same public GitHub repository URL in both stores.

### 1. Install the backend add-on

1. Open **Settings → Add-ons → Add-on Store**.
2. Open the menu, choose **Repositories**, and add this GitHub repository URL.
3. Install **Taiwan MVDIS Penalty Backend**.
4. In the add-on configuration enter:
   - your own national ID;
   - seven-digit ROC birth date, such as `0780702`;
   - query interval (default 24 hours);
   - maximum CAPTCHA attempts (default 3).
5. Start the add-on and enable **Start on boot**.
6. Wait for the log to show that the backend is listening on port 8099.

The initial image build downloads the local OCR runtime and can take several
minutes.

### 2. Install the HACS integration

1. Open **HACS → Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add the same GitHub repository URL and select **Integration**.
4. Download **Taiwan MVDIS Penalty** and restart Home Assistant.
5. Open **Settings → Devices & services → Add integration**.
6. Search for **Taiwan MVDIS Penalty** and confirm that the backend is running.

The first successful result establishes a baseline and deliberately does not
announce historical records as new. Later unseen records trigger a persistent
notification and a `mvdis_penalty_new_case` event.

## Entities

- Unpaid penalty count
- Detected total amount
- Last successful check
- Has unpaid penalty
- Check now

## Event automation example

Replace `notify.mobile_app_your_phone` with your own notification action.

```yaml
alias: New MVDIS penalty
triggers:
  - trigger: event
    event_type: mvdis_penalty_new_case
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "監理服務發現新罰單"
      message: >-
        新增 {{ trigger.event.data.count }} 筆：
        {{ trigger.event.data.summaries | join('；') }}
mode: queued
```

## Privacy and security

- Configure only an identity that belongs to you or that you are authorized to
  manage.
- Add-on options are stored in Home Assistant Supervisor storage and are not
  encrypted at rest. Protect administrator and backup access accordingly.
- The backend does not write the national ID or birth date to logs or state.
- CAPTCHA recognition runs locally using `ddddocr` and `onnxruntime`.
- The API binds only to loopback and is not exposed to the LAN.
- The project never selects a penalty or initiates payment.

## Query behavior

- Default government-site interval: 24 hours.
- Minimum interval: 6 hours.
- Default CAPTCHA attempts: 3.
- Home Assistant polls the local backend every five minutes; this does not query
  the government website.
- MVDIS states that violation records are not updated immediately and appear
  only after the issuing authority enters them.

## Disclaimer

Use at your own risk and respect the MVDIS website's terms, availability, and
capacity. This project provides reminders only. It is not evidence that no
penalty exists and provides no legal, payment, or deadline guarantee.

## Development

```bash
python -m pip install pytest beautifulsoup4 aiohttp requests ruff
pytest
ruff check custom_components mvdis_penalty_backend/app tests
python -m compileall custom_components mvdis_penalty_backend/app
```

## License

MIT

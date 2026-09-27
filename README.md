# Taiwan MVDIS Penalty for Home Assistant

One Home Assistant add-on for automatically checking a user's own unpaid
traffic penalties on Taiwan's official MVDIS website. It performs CAPTCHA OCR
locally on the Home Assistant host, creates entities, and sends a persistent
notification when a new record appears. **HACS is not required.**

> [!WARNING]
> This is an unofficial community project. It is not affiliated with or endorsed
> by the Ministry of Transportation and Communications or the Highway Bureau.
> The government website can change without notice and break the query parser.
> Always verify important information on the official website.

## Compatibility

The current release targets Home Assistant OS on `amd64`, including a Synology
DS723+ running Home Assistant OS in Virtual Machine Manager.

## Installation

1. Open **Settings → Add-ons → Add-on Store**.
2. Open the menu, choose **Repositories**, and add:
   `https://github.com/sunnylinisme/ha-mvdis-penalty`
3. Install **Taiwan MVDIS Penalty**.
4. In the add-on configuration enter:
   - your own national ID;
   - seven-digit ROC birth date, such as `0780702`;
   - query interval (default 24 hours);
   - maximum CAPTCHA attempts (default 3).
5. Start the add-on and enable **Start on boot**.
6. Wait for the log to show a successful query.

The initial image build downloads the local OCR runtime and can take several
minutes. No HACS custom repository or integration restart is needed.

## Home Assistant entities

- `sensor.mvdis_penalty_unpaid_count`
- `sensor.mvdis_penalty_total_amount`
- `binary_sensor.mvdis_penalty_has_unpaid`
- `sensor.mvdis_penalty_last_check`
- `sensor.mvdis_penalty_status`

The first successful result establishes a baseline and deliberately does not
announce historical records as new. Later unseen records create a persistent
notification and fire the `mvdis_penalty_new_case` event.

## Optional mobile notification

The add-on always creates a Home Assistant persistent notification. To forward
the same event to a phone, replace `notify.mobile_app_your_phone` below with the
phone's notification action.

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
- The add-on does not write the national ID or birth date to logs or entity state.
- CAPTCHA recognition runs locally using `ddddocr` and `onnxruntime`.
- No network port is exposed to the LAN.
- The project never selects a penalty or initiates payment.

## Query behavior

- Default government-site interval: 24 hours.
- Minimum interval: 6 hours.
- Default CAPTCHA attempts: 3.
- MVDIS states that violation records are not updated immediately and appear
  only after the issuing authority enters them.

## Disclaimer

Use at your own risk and respect the MVDIS website's terms, availability, and
capacity. This project provides reminders only. It is not evidence that no
penalty exists and provides no legal, payment, or deadline guarantee.

## Development

```bash
python -m pip install pytest beautifulsoup4 requests ruff
pytest
ruff check mvdis_penalty_backend/app tests
python -m compileall mvdis_penalty_backend/app
```

## License

MIT

# Configuration

This single add-on performs the MVDIS query, recognizes the CAPTCHA locally,
creates Home Assistant entities, and sends a persistent notification when a new
record appears. No HACS integration is required.

Configure only an identity that belongs to you or that you are authorized to
manage.

- **National ID number**: Taiwanese national ID, for example `A123456789`.
- **ROC birth date**: Seven digits. ROC year 78, July 2 is `0780702`.
- **Query interval**: Hours between MVDIS queries. Minimum 6; default 24.
- **CAPTCHA attempts**: Local OCR retries per query. Default 3.

Start the add-on and inspect its log. The first container build downloads the
OCR runtime and can take several minutes. A successful query logs only record
counts; identity data is never written to logs.

The first successful query establishes a baseline and does not report existing
records as new. Later unseen records create a Home Assistant persistent
notification and fire the `mvdis_penalty_new_case` event.

Created entities:

- `sensor.mvdis_penalty_unpaid_count`
- `sensor.mvdis_penalty_total_amount`
- `binary_sensor.mvdis_penalty_has_unpaid`
- `sensor.mvdis_penalty_last_check`
- `sensor.mvdis_penalty_status`

The add-on never selects a record or initiates a payment.

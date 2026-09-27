# Configuration

Configure only an identity that belongs to you or that you are authorized to
manage.

- **National ID number**: Taiwanese national ID, for example `A123456789`.
- **ROC birth date**: Seven digits. ROC year 78, July 2 is `0780702`.
- **Query interval**: Hours between MVDIS queries. Minimum 6; default 24.
- **CAPTCHA attempts**: Local OCR retries per query. Default 3.

Start the add-on and inspect its log. The first container build downloads the
OCR runtime and can take several minutes. A successful query logs only the
number of records; identity data is never written to logs.

The API is loopback-only and intended exclusively for the companion HACS
integration. No payment operation is implemented.

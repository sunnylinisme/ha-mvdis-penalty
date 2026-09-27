"""Constants for the Taiwan MVDIS Penalty integration."""

DOMAIN = "mvdis_penalty"

PLATFORMS = ["binary_sensor", "sensor", "button"]

CONF_NOTIFY = "notify"

DEFAULT_NOTIFY = True
BACKEND_URL = "http://127.0.0.1:8099"

EVENT_NEW_PENALTY = "mvdis_penalty_new_case"
STORAGE_VERSION = 1

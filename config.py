import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
STORAGE_DIR = ROOT / "storage"
LOGS_DIR = ROOT / "logs"
STORAGE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

STORAGE_STATE_PATH = STORAGE_DIR / "storage_state.json"

ALLEGRO_EMAIL = os.getenv("ALLEGRO_EMAIL", "")
ALLEGRO_PASSWORD = os.getenv("ALLEGRO_PASSWORD", "")

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = (
    '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="24"'
)
DEFAULT_SEC_CH_UA_PLATFORM = '"Windows"'

BROWSER_USER_AGENT = os.getenv("BROWSER_USER_AGENT", DEFAULT_UA)
BROWSER_SEC_CH_UA = os.getenv("BROWSER_SEC_CH_UA", DEFAULT_SEC_CH_UA)
BROWSER_SEC_CH_UA_PLATFORM = os.getenv(
    "BROWSER_SEC_CH_UA_PLATFORM", DEFAULT_SEC_CH_UA_PLATFORM
)

SALES_CENTER_URL = "https://salescenter.allegro.com/my-sales"
EDGE_API = "https://edge.salescenter.allegro.com"

# Random delay between processed rows. Keeps us human-like and reduces
# Datadome / rate-limit risk. Used as: random.uniform(MIN, MAX)
ROW_DELAY_MIN = float(os.getenv("ROW_DELAY_MIN", "2.0"))
ROW_DELAY_MAX = float(os.getenv("ROW_DELAY_MAX", "4.0"))

# Every N rows, take a longer break (simulates a human pausing).
LONG_PAUSE_EVERY_N_ROWS = int(os.getenv("LONG_PAUSE_EVERY_N_ROWS", "100"))
LONG_PAUSE_MIN = float(os.getenv("LONG_PAUSE_MIN", "20.0"))
LONG_PAUSE_MAX = float(os.getenv("LONG_PAUSE_MAX", "45.0"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_admin_raw = os.getenv("TELEGRAM_ADMIN_IDS", "")
TELEGRAM_ADMIN_IDS: set[int] = {
    int(x) for x in _admin_raw.replace(" ", "").split(",") if x.strip().isdigit()
}

OUTPUT_DIR = ROOT / "output"
RUNS_DIR = STORAGE_DIR / "runs"
SHEETS_DIR = STORAGE_DIR / "sheets"
OUTPUT_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)
SHEETS_DIR.mkdir(exist_ok=True)

STATE_PATH = STORAGE_DIR / "state.json"

"""Constants for MZKZG Transport integration."""

DOMAIN = "mzkzg_transport"

# API endpoints
ZTM_GDANSK_DEPARTURES_URL = "https://ckan2.multimediagdansk.pl/departures"
ZTM_GDANSK_STOPS_URL = (
    "https://mapa.ztm.gda.pl/dataset/"
    "c24aa637-3619-4dc2-a171-a23eec8f2172/resource/"
    "4c4025f0-01bf-41f7-a39f-d156d201b82b/download/stops.json"
)

ZKM_GDYNIA_DELAYS_URL = "https://api.zdiz.gdynia.pl/pt/delays"
ZKM_GDYNIA_STOPS_URL = "https://api.zdiz.gdynia.pl/pt/stops"
ZKM_GDYNIA_ROUTES_URL = "https://api.zdiz.gdynia.pl/pt/routes"

# Providers
PROVIDER_ZTM = "ztm_gdansk"
PROVIDER_ZKM = "zkm_gdynia"
PROVIDER_MZK = "mzk_wejherowo"
PROVIDER_PLK = "plk_rail"

CONF_STOPS = "stops"
CONF_STOP_ID = "stop_id"
CONF_PROVIDER = "provider"
CONF_NAME = "name"
CONF_API_KEY = "api_key"
CONF_PLK_TIER = "plk_tier"

DEFAULT_SCAN_INTERVAL = 30

# PLK tier → max requests per hour
PLK_TIER_LIMITS = {
    "basic": 100,
    "standard": 500,
    "premium": 2000,
}

MZK_GTFS_URL = "https://mkuran.pl/gtfs/wejherowo.zip"
PLK_API_BASE = "https://pdp-api.plk-sa.pl/api/v1"

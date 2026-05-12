"""Data coordinator for MZKZG Transport."""

from datetime import datetime, timedelta
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLK_API_BASE,
    PROVIDER_MZK,
    PROVIDER_PLK,
    PROVIDER_ZKM,
    PROVIDER_ZTM,
    ZKM_GDYNIA_DELAYS_URL,
    ZKM_GDYNIA_ROUTES_URL,
    ZTM_GDANSK_DEPARTURES_URL,
)

_LOGGER = logging.getLogger(__name__)


class MzkzgTransportCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches departure data from ZTM/ZKM APIs."""

    def __init__(
        self,
        hass: HomeAssistant,
        stop_id: str,
        provider: str,
        name: str,
        api_key: str = "",
        plk_tier: str = "basic",
    ) -> None:
        """Initialize coordinator."""
        from .const import PLK_TIER_INTERVALS
        plk_interval = PLK_TIER_INTERVALS.get(plk_tier, 180)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{stop_id}",
            update_interval=timedelta(seconds=plk_interval if provider == PROVIDER_PLK else DEFAULT_SCAN_INTERVAL),
        )
        self.stop_id = stop_id
        self.provider = provider
        self.stop_name = name
        self.api_key = api_key
        self._routes_map: dict[str, str] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self.hass)

    async def _async_update_data(self) -> dict:
        """Fetch departures from the appropriate API."""
        try:
            if self.provider == PROVIDER_ZTM:
                return await self._fetch_ztm()
            if self.provider == PROVIDER_MZK:
                return await self._fetch_mzk()
            if self.provider == PROVIDER_PLK:
                return await self._fetch_plk()
            return await self._fetch_zkm()
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def _fetch_ztm(self) -> dict:
        """Fetch departures from ZTM Gdańsk TRISTAR API."""
        session = await self._get_session()
        url = f"{ZTM_GDANSK_DEPARTURES_URL}?stopId={self.stop_id}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            data = await resp.json()

        departures = []
        now = datetime.now()
        for d in data.get("departures", []):
            estimated = d.get("estimatedTime") or d.get("theoreticalTime")
            if not estimated:
                continue
            dep_time = datetime.fromisoformat(estimated.replace("Z", "+00:00")) if "T" in estimated else None
            if dep_time and dep_time.timestamp() < now.timestamp() - 30:
                continue

            delay_sec = d.get("delayInSeconds") or d.get("delay") or 0
            status = d.get("status", "SCHEDULED")
            departures.append({
                "route": str(d.get("routeShortName") or d.get("routeId") or "?"),
                "headsign": d.get("headsign") or d.get("tripHeadsign") or "—",
                "estimated_time": estimated,
                "theoretical_time": d.get("theoreticalTime"),
                "delay_seconds": delay_sec,
                "realtime": status == "REALTIME",
                "vehicle_type": self._ztm_vehicle_type(d.get("routeShortName") or d.get("routeId")),
                "bike_allowed": d.get("bikeAllowed", None),
                "wheelchair_accessible": d.get("wheelchairAccessible", None),
                "air_conditioning": d.get("airConditioning", None),
                "provider": PROVIDER_ZTM,
            })

        departures.sort(key=lambda x: x.get("estimated_time") or "")
        return {
            "stop_id": self.stop_id,
            "stop_name": self.stop_name,
            "provider": PROVIDER_ZTM,
            "departures": departures,
            "last_update": now.isoformat(),
        }

    async def _fetch_zkm(self) -> dict:
        """Fetch departures from ZKM Gdynia ZDiZ API."""
        session = await self._get_session()

        # Load routes map if empty
        if not self._routes_map:
            await self._load_zkm_routes(session)

        url = f"{ZKM_GDYNIA_DELAYS_URL}?stopId={self.stop_id}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            data = await resp.json()

        departures = []
        now = datetime.now()
        for d in data.get("delay", []):
            route_id = d.get("routeId") or d.get("routeShortName")
            route_name = self._routes_map.get(str(route_id), str(route_id))
            time_str = d.get("estimatedTime") or d.get("theoreticalTime") or d.get("time")
            if not time_str:
                continue

            # ZDiZ returns HH:MM:SS format
            estimated_iso = time_str
            if len(time_str) <= 8 and ":" in time_str:
                parts = time_str.split(":")
                h, m = int(parts[0]), int(parts[1])
                s = int(parts[2]) if len(parts) > 2 else 0
                day_add = 0
                if h >= 24:
                    h -= 24
                    day_add = 1
                dep_dt = now.replace(hour=h, minute=m, second=s, microsecond=0) + timedelta(days=day_add)
                if (dep_dt - now).total_seconds() < -3600:
                    dep_dt += timedelta(days=1)
                if (dep_dt - now).total_seconds() < -30:
                    continue
                estimated_iso = dep_dt.isoformat()

            delay_sec = d.get("delayInSeconds") or d.get("delay") or 0
            status = d.get("status", "")
            is_realtime = bool(
                status == "REALTIME"
                or d.get("estimatedTime")
                or d.get("realTime")
                or d.get("predictedTime")
            )

            departures.append({
                "route": route_name,
                "headsign": d.get("headsign") or d.get("direction") or d.get("directionName") or "—",
                "estimated_time": estimated_iso,
                "theoretical_time": d.get("theoreticalTime") or time_str,
                "delay_seconds": delay_sec,
                "realtime": is_realtime,
                "vehicle_type": self._zkm_vehicle_type(route_name),
                "bike_allowed": d.get("bikeAllowed", None),
                "wheelchair_accessible": d.get("wheelchairAccessible", None),
                "air_conditioning": d.get("airConditioning", None),
                "provider": PROVIDER_ZKM,
            })

        departures.sort(key=lambda x: x.get("estimated_time") or "")
        return {
            "stop_id": self.stop_id,
            "stop_name": self.stop_name,
            "provider": PROVIDER_ZKM,
            "departures": departures,
            "last_update": now.isoformat(),
        }

    async def _fetch_plk(self) -> dict:
        """Fetch departures from PLK OpenData API (PR/SKM/IC)."""
        session = await self._get_session()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        departures = []

        # Share operations data across all PLK coordinators to reduce API calls
        plk_cache = self.hass.data[DOMAIN].setdefault("_plk_cache", {})
        cache_age = (now - plk_cache.get("_ts", datetime.min)).total_seconds()

        if cache_age > 60:  # Refresh shared operations cache every 60s
            # Collect all PLK station IDs
            all_plk_stations = set()
            for coord in self.hass.data[DOMAIN].get("_coordinators", {}).values():
                if coord.provider == PROVIDER_PLK:
                    all_plk_stations.add(coord.stop_id)
            stations_param = ",".join(all_plk_stations)

            try:
                ops_url = f"{PLK_API_BASE}/operations"
                params = {
                    "stations": stations_param,
                    "withPlanned": "true",
                    "fullRoutes": "true",
                    "pageSize": "100",
                }
                async with session.get(
                    ops_url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 429:
                        _LOGGER.warning("PLK API rate limit hit, skipping this cycle")
                        # Use stale cache if available
                        if not plk_cache.get("_data"):
                            raise UpdateFailed("PLK API: przekroczono limit zapytań (429). Dane odświeżą się automatycznie.")
                    elif resp.status == 200:
                        plk_cache["_data"] = await resp.json()
                        plk_cache["_ts"] = now
            except UpdateFailed:
                raise
            except Exception:
                _LOGGER.debug("PLK operations fetch failed for %s", stations_param)

        ops_data = plk_cache.get("_data")

        # Fetch schedules (planned) — per station
        sched_data = None
        try:
            sched_url = f"{PLK_API_BASE}/schedules"
            params = {
                "stations": self.stop_id,
                "dateFrom": today,
                "dateTo": today,
                "dictionaries": "true",
                "fullRoute": "true",
            }
            async with session.get(
                sched_url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 429:
                    _LOGGER.warning("PLK API rate limit hit on schedules")
                elif resp.status == 200:
                    sched_data = await resp.json()
        except Exception:
            _LOGGER.debug("PLK schedules fetch failed for %s", self.stop_id)

        # Resolve station name
        if not self.stop_name and sched_data:
            dicts = sched_data.get("dictionaries", {})
            stations = dicts.get("stations", {})
            entry = stations.get(str(self.stop_id), {})
            self.stop_name = entry if isinstance(entry, str) else entry.get("name", f"Stacja {self.stop_id}")

        # Build realtime map from operations — index by multiple keys for matching
        rt_map: dict[str, dict] = {}
        if ops_data:
            # Operations API may return trains under different keys
            trains_list = ops_data.get("trains") or ops_data.get("routes") or ops_data.get("items") or []
            if not trains_list and isinstance(ops_data.get("data"), dict):
                trains_list = ops_data["data"].get("trains", [])
            if not trains_list and isinstance(ops_data, list):
                trains_list = ops_data
            _LOGGER.debug(
                "PLK operations for %s: top-level keys=%s, trains=%d",
                self.stop_id,
                list(ops_data.keys()) if isinstance(ops_data, dict) else "list",
                len(trains_list) if trains_list else 0,
            )
            for train in (trains_list or []):
                for st in train.get("stations", []):
                    if str(st.get("stationId")) == str(self.stop_id):
                        rt_info = {
                            "delay": st.get("departureDelay") or st.get("arrivalDelay") or 0,
                            "platform": st.get("platform") or st.get("track") or "",
                            "cancelled": st.get("cancelled", False),
                        }
                        # Index by all possible identifiers
                        for k in ("trainNumber", "nationalNumber", "orderId", "trainOrderId"):
                            v = train.get(k)
                            if v:
                                rt_map[str(v)] = rt_info
                        break
            _LOGGER.debug("PLK rt_map keys for %s: %s", self.stop_id, list(rt_map.keys())[:10])

        # Parse schedule data
        if sched_data:
            dicts = sched_data.get("dictionaries", {})
            carriers = dicts.get("carriers", {})
            stations_dict = dicts.get("stations", {})

            for route in sched_data.get("routes", []):
                route_stations = route.get("stations", [])
                carrier_code = route.get("carrierCode", "")
                carrier_name = carriers.get(carrier_code, carrier_code)
                train_number = str(
                    route.get("nationalNumber")
                    or route.get("trainOrderId")
                    or route.get("orderId")
                    or route.get("trainNumber")
                    or ""
                )
                category = route.get("commercialCategorySymbol", carrier_code)

                for i, stop in enumerate(route_stations):
                    if str(stop.get("stationId")) != str(self.stop_id):
                        continue

                    dep_time_str = stop.get("departureTime")
                    if not dep_time_str:
                        continue

                    # Parse HH:MM:SS duration into datetime
                    operating_date = route.get("operatingDate", today)
                    dep_dt = self._plk_time_to_datetime(
                        operating_date, dep_time_str, stop.get("departureDay", 0)
                    )
                    if dep_dt < now - timedelta(minutes=2):
                        continue

                    # Destination = last station
                    dest_stations = route_stations[i + 1:]
                    dest_id = dest_stations[-1]["stationId"] if dest_stations else ""
                    dest_entry = stations_dict.get(str(dest_id), "")
                    destination = dest_entry if isinstance(dest_entry, str) else dest_entry.get("name", "")

                    # Realtime info — try matching by multiple identifiers
                    rt = {}
                    is_realtime = False
                    for candidate in (
                        train_number,
                        str(route.get("nationalNumber") or ""),
                        str(route.get("orderId") or ""),
                        str(route.get("trainOrderId") or ""),
                        str(route.get("trainNumber") or ""),
                        str(stop.get("departureTrainNumber") or ""),
                    ):
                        if candidate and candidate in rt_map:
                            rt = rt_map[candidate]
                            is_realtime = True
                            break
                    delay_min = rt.get("delay", 0)
                    is_cancelled = rt.get("cancelled", False)

                    # Build route stops list (from current station onwards)
                    route_stops = []
                    for rs in route_stations[i:]:
                        rs_id = str(rs.get("stationId", ""))
                        rs_entry = stations_dict.get(rs_id, "")
                        rs_name = rs_entry if isinstance(rs_entry, str) else rs_entry.get("name", rs_id)
                        route_stops.append(rs_name)

                    departures.append({
                        "route": category or "R",
                        "headsign": destination or "—",
                        "estimated_time": (dep_dt + timedelta(minutes=delay_min)).isoformat() if is_realtime else dep_dt.isoformat(),
                        "theoretical_time": dep_dt.isoformat(),
                        "delay_seconds": delay_min * 60,
                        "realtime": is_realtime,
                        "vehicle_type": "train",
                        "bike_allowed": None,
                        "wheelchair_accessible": None,
                        "air_conditioning": None,
                        "carrier": carrier_name,
                        "category": category,
                        "train_number": str(stop.get("departureTrainNumber") or train_number or ""),
                        "platform": rt.get("platform", stop.get("platform", "")),
                        "cancelled": is_cancelled,
                        "route_stops": route_stops,
                        "provider": PROVIDER_PLK,
                    })
                    break

        departures.sort(key=lambda x: x.get("estimated_time") or "")
        departures = departures[:30]  # Limit to nearest 30
        return {
            "stop_id": self.stop_id,
            "stop_name": self.stop_name or f"Stacja {self.stop_id}",
            "provider": PROVIDER_PLK,
            "departures": departures,
            "last_update": now.isoformat(),
        }

    @staticmethod
    def _plk_time_to_datetime(operating_date: str, time_str: str, day_offset: int = 0) -> datetime:
        """Convert PLK time (HH:MM:SS duration) + operating date to datetime."""
        parts = time_str.replace("PT", "").replace("H", ":").replace("M", ":").replace("S", "").split(":")
        if len(parts) >= 2:
            h, m = int(parts[0]), int(parts[1])
            s = int(parts[2]) if len(parts) > 2 else 0
        else:
            h, m, s = 0, 0, 0
        base = datetime.strptime(operating_date[:10], "%Y-%m-%d")
        return base.replace(hour=0, minute=0, second=0) + timedelta(days=day_offset, hours=h, minutes=m, seconds=s)

    async def _fetch_mzk(self) -> dict:
        """Fetch departures from MZK Wejherowo static GTFS."""
        from .gtfs_provider import get_gtfs_data

        gtfs = await get_gtfs_data()
        now = datetime.now()

        if not self.stop_name:
            stop_info = gtfs.stops.get(self.stop_id, {})
            self.stop_name = stop_info.get("name", f"Przystanek {self.stop_id}")

        departures = gtfs.get_departures(self.stop_id, now)
        return {
            "stop_id": self.stop_id,
            "stop_name": self.stop_name,
            "provider": PROVIDER_MZK,
            "departures": departures,
            "last_update": now.isoformat(),
        }

    async def _load_zkm_routes(self, session: aiohttp.ClientSession) -> None:
        """Load ZKM route short names."""
        try:
            async with session.get(
                ZKM_GDYNIA_ROUTES_URL, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    routes = data if isinstance(data, list) else data.get("value", [])
                    for r in routes:
                        if r.get("routeId") and r.get("routeShortName"):
                            self._routes_map[str(r["routeId"])] = str(r["routeShortName"])
        except Exception:
            _LOGGER.warning("Could not load ZKM routes")

    @staticmethod
    def _ztm_vehicle_type(route_id) -> str:
        """Determine vehicle type for ZTM Gdańsk."""
        s = str(route_id or "")
        n = int(s) if s.isdigit() else None
        if n is not None and n < 100:
            return "tram"
        return "bus"

    @staticmethod
    def _zkm_vehicle_type(route_name) -> str:
        """Determine vehicle type for ZKM Gdynia."""
        s = str(route_name or "")
        n = int(s) if s.isdigit() else None
        if n is not None and 20 <= n <= 29:
            return "trolleybus"
        return "bus"



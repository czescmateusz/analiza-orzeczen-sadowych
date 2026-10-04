"""Thin client for the public SAOS API (https://www.saos.org.pl/help/index.php/dokumentacja-api).

The search endpoint returns only metadata and a text snippet, so judgments are
collected in two steps: search to gather ids, then fetch each full record.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import requests

BASE_URL = "https://www.saos.org.pl/api"

# Full-text queries used to find candidate road-accident compensation cases.
# Recall matters more than precision here: classify.py filters the results.
ROAD_ACCIDENT_QUERIES = [
    "wypadek komunikacyjny zadośćuczynienie",
    "wypadek drogowy zadośćuczynienie",
    "kolizja drogowa zadośćuczynienie",
    "potrącenie pieszego zadośćuczynienie",
    "ubezpieczenie OC posiadacza pojazdu zadośćuczynienie",
    "wypadek komunikacyjny renta",
    "wypadek komunikacyjny śmierć osoby najbliższej",
]

# Common courts only (SN rarely sets amounts), judgments only (no decisions/orders).
SEARCH_FILTERS = {"courtType": "COMMON", "judgmentTypes": "SENTENCE"}

log = logging.getLogger(__name__)


class SaosError(RuntimeError):
    pass


class SaosClient:
    def __init__(self, delay: float = 0.3, timeout: float = 60, max_retries: int = 5):
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "analiza-orzeczen-sadowych/0.1 (research project)"
        self._last_request = 0.0
        self.maintenance_wait = 300
        self.max_maintenance_waits = 24
        self._maintenance_waits = 0

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        url = f"{BASE_URL}{path}"
        attempt = 0
        while attempt < self.max_retries:
            wait = self.delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                problem = str(exc)
            else:
                if resp.status_code == 200:
                    if "Przerwa techniczna" in resp.text[:500]:
                        self._wait_for_maintenance()  # doesn't count as a failed attempt
                        continue
                    try:
                        data = resp.json()
                    except requests.JSONDecodeError:
                        problem = "non-JSON body"
                    else:
                        self._maintenance_waits = 0
                        return data
                elif resp.status_code == 404:
                    return None
                elif resp.status_code != 429 and resp.status_code < 500:
                    raise SaosError(f"{resp.status_code} for {resp.url}: {resp.text[:200]}")
                else:
                    problem = f"status {resp.status_code}"
            attempt += 1
            log.warning("%s failed (%s), attempt %d/%d", url, problem, attempt, self.max_retries)
            time.sleep(min(60, 2**attempt))
        raise SaosError(f"giving up on {url} after {self.max_retries} attempts")

    def count(self, query: str, **filters: str) -> int:
        data = self._get("/search/judgments", {"all": query, "pageSize": 10, **filters})
        return data["info"]["totalResults"] if data else 0

    def _wait_for_maintenance(self) -> None:
        """SAOS serves a 'Przerwa techniczna' page with status 200 during maintenance."""
        self._maintenance_waits += 1
        if self._maintenance_waits > self.max_maintenance_waits:
            raise SaosError("SAOS still under maintenance, giving up")
        log.warning("SAOS under maintenance, waiting %d s (%d/%d)",
                    self.maintenance_wait, self._maintenance_waits, self.max_maintenance_waits)
        time.sleep(self.maintenance_wait)

    def search(self, query: str, page_size: int = 100, **filters: str) -> Iterator[dict]:
        """Yield search hits (metadata and snippet, no full text) for a full-text query."""
        page = 0
        while True:
            params = {"all": query, "pageSize": page_size, "pageNumber": page, **filters}
            data = self._get("/search/judgments", params)
            items = data["items"] if data else []
            if page == 0 and data:
                log.info("query %r: %d results", query, data["info"]["totalResults"])
            yield from items
            if not items or not any(link["rel"] == "next" for link in data["links"]):
                return
            page += 1

    def judgment(self, judgment_id: int) -> dict | None:
        """Return the full judgment record, or None if SAOS no longer has it."""
        data = self._get(f"/judgments/{judgment_id}")
        return data["data"] if data else None

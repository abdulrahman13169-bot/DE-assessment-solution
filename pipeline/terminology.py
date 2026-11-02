"""Optional terminology enrichment via NLM RxNav (public, no API key).

Enabled with `--rxnav`. Resolves
  * claim NDC11  -> RxCUI + name         (/REST/ndcstatus.json?ndc=...)
  * any RxCUI    -> ingredient(s) (IN)   (/REST/rxcui/{rxcui}/related.json?tty=IN)
so that the C-CDA med list (RxNorm SCD/SBD) and pharmacy fills (NDC) can be
compared at the *ingredient* level, which is what clinically matters
("atorvastatin 20 mg tab" on the list vs. a fill of a different manufacturer's NDC).

Responses are cached in reference/rxnav_cache.json, so re-runs are offline,
deterministic and reviewable; commit the cache to make the run reproducible.
The pipeline still runs end-to-end without this step (offline learned crosswalks).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)
BASE = "https://rxnav.nlm.nih.gov/REST"


class RxNav:
    def __init__(self, cache_path: str, offline: bool = False, min_interval: float = 0.07):
        self.cache_path = cache_path
        self.offline = offline
        self.min_interval = min_interval  # NLM asks for <= 20 requests/second
        self._last = 0.0
        self.cache = {}
        if os.path.exists(cache_path):
            with open(cache_path) as fh:
                self.cache = json.load(fh)
        self.errors = 0

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w") as fh:
            json.dump(self.cache, fh, indent=1, sort_keys=True)

    def _get(self, path: str, params: dict | None = None):
        url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        if url in self.cache:
            return self.cache[url]
        if self.offline or self.errors >= 5:  # circuit breaker: stop hammering if NLM unreachable
            return None
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 (fixed https host)
                data = json.loads(r.read().decode())
        except Exception as exc:
            self.errors += 1
            log.warning("RxNav request failed (%s): %s", url, exc)
            return None
        finally:
            self._last = time.time()
        self.cache[url] = data
        return data

    # ------------------------------------------------------------------ #
    def ndc(self, ndc11: str) -> dict:
        """-> {'rxcui':..., 'name':..., 'status':...} (values may be None)."""
        d = self._get("/ndcstatus.json", {"ndc": ndc11}) or {}
        st = d.get("ndcStatus") or {}
        rxcui = st.get("rxcui") or None
        if not rxcui:  # obsolete NDCs: fall back to the most recent active RxCUI in history
            for h in st.get("ndcHistory") or []:
                if h.get("activeRxcui"):
                    rxcui = h["activeRxcui"]
                    break
        return {"rxcui": rxcui, "name": st.get("conceptName") or None, "status": st.get("status")}

    def ingredients(self, rxcui: str) -> list[str]:
        d = self._get(f"/rxcui/{rxcui}/related.json", {"tty": "IN"}) or {}
        names = []
        for grp in (d.get("relatedGroup") or {}).get("conceptGroup") or []:
            for cp in grp.get("conceptProperties") or []:
                if cp.get("name"):
                    names.append(cp["name"].lower())
        return sorted(set(names))


def enrich(rx_ndcs, rxcuis, client: RxNav) -> tuple[dict, dict]:
    """Return ({ndc11: {rxcui,name,status}}, {rxcui: 'ingredient+ingredient'})."""
    ndc_map = {n: client.ndc(n) for n in sorted(set(x for x in rx_ndcs if isinstance(x, str)))}
    all_rxcui = set(x for x in rxcuis if isinstance(x, str)) | {v["rxcui"] for v in ndc_map.values() if v["rxcui"]}
    ing_map = {}
    for c in sorted(all_rxcui):
        ing = client.ingredients(c)
        if ing:
            ing_map[c] = "+".join(ing)
    client.save()
    log.info("RxNav: %d/%d NDCs resolved, %d RxCUIs with ingredients (errors=%d)",
             sum(1 for v in ndc_map.values() if v["rxcui"]), len(ndc_map), len(ing_map), client.errors)
    return ndc_map, ing_map

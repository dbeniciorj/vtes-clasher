"""
events_common.py

Funções partilhadas pelos jobs que atualizam ../vtes-sim-site/events.json:
  * update_vekn_events.py    (fonte principal: calendário VEKN)
  * update_vtes_es_events.py (fonte extra: Liga Española, vtes.es)

Só usa a biblioteca padrão do Python 3.8+.
"""

from __future__ import annotations

import difflib
import http.client
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuração comum
# ---------------------------------------------------------------------------

EVENTS_FILE = Path(__file__).resolve().parent.parent / "vtes-sim-site" / "events.json"

USER_AGENT = "vtes-sim-calendar-bot/1.0 (static community calendar)"
REQUEST_DELAY_SECONDS = 0.5  # pausa entre pedidos, para não sobrecarregar os sites
TIMEOUT_SECONDS = 20
RETRIES = 3

COUNTRY_NAMES = {
    "PT": "Portugal",
    "ES": "Spain",
    "US": "United States",
    "BR": "Brazil",
}

SOURCE_VEKN = "vekn"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_last_request_at = 0.0


def fetch(url: str) -> str:
    """GET com pausa entre pedidos e tentativas extra em caso de erro."""
    global _last_request_at
    last_error: Exception | None = None

    for attempt in range(1, RETRIES + 1):
        wait = REQUEST_DELAY_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                break  # não vale a pena repetir
        except (OSError, http.client.HTTPException) as exc:
            last_error = exc

        if attempt < RETRIES:
            time.sleep(2 * attempt)

    raise RuntimeError(f"falha ao obter {url}: {last_error}")


# ---------------------------------------------------------------------------
# events.json
# ---------------------------------------------------------------------------


def load_payload(path: Path = EVENTS_FILE) -> dict:
    """
    Lê o events.json. Aceita o formato antigo (lista) e o novo ({"events": [...]}).
    Devolve sempre um dict com a chave "events" (lista de dicts com "id").
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"events": []}
    if isinstance(data, list):
        data = {"events": data}
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return {"events": []}
    data["events"] = [ev for ev in data["events"] if isinstance(ev, dict) and ev.get("id")]
    return data


def event_source(ev: dict) -> str:
    """Eventos sem campo "source" (versões antigas do ficheiro) são da VEKN."""
    return ev.get("source") or SOURCE_VEKN


def sort_events(events: list[dict]) -> list[dict]:
    return sorted(
        events,
        key=lambda e: (e.get("date") or "", e.get("time") or "", e.get("country") or "", e.get("title") or ""),
    )


def write_json_atomic(path: Path, payload: dict) -> None:
    """Escreve num ficheiro temporário e renomeia (nunca deixa o JSON a meio)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".events-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Deduplicação entre fontes
# ---------------------------------------------------------------------------


def normalize(text: str | None) -> str:
    """Minúsculas, sem acentos nem pontuação: 'Cónclave de la Camarilla!' -> 'conclave de la camarilla'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _titles_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = sorted((na, nb), key=len)
    if len(shorter) >= 8 and shorter in longer:
        return True
    if difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return len(ta & tb) / len(ta | tb) >= 0.6


def _city(ev: dict) -> str:
    """1.ª parte do 'location' ('Sabadell, Catalunya, Spain' -> 'sabadell'); vazio se só tiver o país."""
    location = ev.get("location") or ""
    if "," not in location:
        return ""
    return normalize(location.split(",")[0])


def _venues_match(a: str | None, b: str | None) -> bool:
    va, vb = normalize(a), normalize(b)
    if not va or not vb:
        return False
    shorter, longer = sorted((va, vb), key=len)
    return va == vb or (len(shorter) >= 6 and shorter in longer)


def is_same_event(a: dict, b: dict) -> bool:
    """
    Dois eventos de fontes diferentes são o mesmo torneio quando têm a mesma data
    e (nome parecido OU mesmo local OU mesma cidade).
    """
    if not a.get("date") or a.get("date") != b.get("date"):
        return False
    if _titles_match(a.get("title"), b.get("title")):
        return True
    if _venues_match(a.get("venue"), b.get("venue")):
        return True
    city_a, city_b = _city(a), _city(b)
    return bool(city_a) and city_a == city_b

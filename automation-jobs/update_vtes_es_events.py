#!/usr/bin/env python3
"""
update_vtes_es_events.py

Acrescenta ao ../vtes-sim-site/events.json os torneios da Liga Española
(Iberian Conclave, https://vtes.es) da temporada configurada em SEASON_URL.

Uso (sem parâmetros), DEPOIS do update_vekn_events.py:
    python3 automation-jobs/update_vekn_events.py
    python3 automation-jobs/update_vtes_es_events.py

Só usa a biblioteca padrão do Python 3.8+ (não precisa de pip install).

Comportamento:
  * Lê a lista de torneios jogados (SEASON_URL) e anunciados (SEASON_URL/announced).
  * Abre a página de cada torneio para obter data, horário, local, cidade e país.
  * Eventos repetidos: a VEKN tem prioridade. Um torneio do vtes.es é ignorado
    quando já existe um evento VEKN na mesma data com nome parecido, mesmo
    local ou mesma cidade (regra em events_common.is_same_event).
  * Os eventos vtes.es da execução anterior são substituídos pelos novos.
  * Torneios cancelados são ignorados.
  * Se alguma lista de torneios falhar, NÃO altera o events.json e sai com código 1.
  * Se a página de um torneio falhar, reaproveita os dados da execução anterior.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # não criar __pycache__ ao importar events_common

import datetime as dt  # noqa: E402
import re  # noqa: E402
import urllib.parse  # noqa: E402
from html.parser import HTMLParser  # noqa: E402

from events_common import (  # noqa: E402
    COUNTRY_NAMES,
    EVENTS_FILE,
    SOURCE_VEKN,
    event_source,
    fetch,
    is_same_event,
    load_payload,
    sort_events,
    write_json_atomic,
)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# Atualizar no início de cada temporada (ver https://vtes.es/en/league/season)
SEASON_URL = "https://vtes.es/en/league/season/5/2026-2027/tournament"
LIST_URLS = [SEASON_URL, SEASON_URL + "/announced"]

BASE_URL = "https://vtes.es"
SOURCE = "vtes.es"
DEFAULT_COUNTRY = "ES"

TOURNAMENT_HREF_RE = re.compile(r"/league/tournament/(\d+)/([^/]+)/view")
COUNTRY_SUFFIX_RE = re.compile(r"\s([A-Z]{2})\s*$")

# Formato do vtes.es -> tipo usado pela VEKN
FORMAT_TYPES = {
    "standard": "Standard Constructed",
    "limited": "Limited",
}

# Palavras que ficam em minúsculas a meio do título / siglas que ficam em maiúsculas
SMALL_WORDS = {"a", "al", "and", "at", "con", "da", "das", "de", "del", "do", "dos", "e",
               "el", "en", "for", "in", "la", "las", "los", "of", "on", "para", "por",
               "the", "to", "y"}
KEEP_UPPER = {"VTES", "V:TES", "GP", "NC", "CC", "ECQ", "NAC", "TWD"}
ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"}


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def smart_title(title: str) -> str:
    """
    O vtes.es publica os nomes em MAIÚSCULAS. Converte para um formato parecido
    com o da VEKN: 'LIGA GL/PT 26-27 - A NEW NIGHT' -> 'Liga GL/PT 26-27 - A New Night'.
    Títulos que já têm minúsculas ficam como estão.
    """
    if title != title.upper():
        return title

    words = title.split()
    out = []
    for i, word in enumerate(words):
        core = word.strip("-:.,;!?()¡¿\"'")
        prev = words[i - 1] if i else ""
        after_break = (not prev) or prev == "-" or prev[-1:] in "-:.!?"
        if any(ch.isdigit() for ch in word) or "/" in word or core in KEEP_UPPER or core in ROMAN:
            out.append(word)
        elif core.lower() in SMALL_WORDS and not after_break:
            out.append(word.lower())
        else:
            lower = word.lower()
            # 1.ª letra (depois de pontuação inicial) em maiúscula, também após hífen
            out.append(re.sub(r"(^[\W_]*|-)([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), lower))
    return " ".join(out)


# ---------------------------------------------------------------------------
# Lista de torneios (jogados / anunciados)
# ---------------------------------------------------------------------------


class TournamentListParser(HTMLParser):
    """Linhas de <tbody>: Nome (link) | Região | Data (<time>) | [Jogadores] | Estado."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._in_tbody = False
        self._row: dict | None = None
        self._cell: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self._in_tbody = True
        elif tag == "tr" and self._in_tbody:
            self._row = {"cells": [], "id": None, "slug": None, "date": None}
        elif tag == "td" and self._row is not None:
            self._cell = ""
        elif tag == "a" and self._row is not None and self._row["id"] is None:
            match = TOURNAMENT_HREF_RE.search(attrs.get("href") or "")
            if match:
                self._row["id"], self._row["slug"] = match.group(1), match.group(2)
        elif tag == "time" and self._row is not None and attrs.get("datetime"):
            self._row["date"] = attrs["datetime"][:10]

    def handle_endtag(self, tag):
        if tag == "tbody":
            self._in_tbody = False
        elif tag == "td" and self._row is not None and self._cell is not None:
            self._row["cells"].append(clean(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row["id"] and len(self._row["cells"]) >= 3:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data


def scrape_list(url: str) -> list[dict]:
    parser = TournamentListParser()
    parser.feed(fetch(url))
    tournaments = []
    for row in parser.rows:
        cells = row["cells"]
        tournaments.append({
            "id": row["id"],
            "slug": row["slug"],
            "title": cells[0],
            "league": cells[1] or None,
            "date": row["date"],
            "status": cells[-1],
        })
    return tournaments


# ---------------------------------------------------------------------------
# Página de um torneio
# ---------------------------------------------------------------------------


class TournamentDetailParser(HTMLParser):
    """
    Lê as tabelas 'table-ledger' (<th>Rótulo</th><td>valor</td>) e guarda, por
    rótulo, o texto, os links e os atributos datetime de <time>.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, dict] = {}
        self._th: str | None = None
        self._label: str | None = None
        self._td: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._label = None
        elif tag == "th":
            self._th = ""
        elif tag == "td" and self._label:
            self._td = {"text": "", "hrefs": [], "datetimes": []}
        elif self._td is not None:
            if tag == "a" and attrs.get("href"):
                self._td["hrefs"].append(attrs["href"])
            elif tag == "time" and attrs.get("datetime"):
                self._td["datetimes"].append(attrs["datetime"])

    def handle_endtag(self, tag):
        if tag == "th" and self._th is not None:
            self._label = clean(self._th).lower()
            self._th = None
        elif tag == "td" and self._td is not None:
            self._td["text"] = clean(self._td["text"])
            self.fields.setdefault(self._label, self._td)
            self._td = None

    def handle_data(self, data):
        if self._th is not None:
            self._th += data
        elif self._td is not None:
            self._td["text"] += data


def country_from_map_link(href: str) -> str | None:
    """O link do Google Maps termina com o código do país: '...query=Snafu ... Sabadell ES'."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("query", [""])[0]
    match = COUNTRY_SUFFIX_RE.search(query)
    return match.group(1) if match else None


def country_from_address(address: str) -> str | None:
    """Pelo formato do código postal: PT = 'NNNN-NNN' (4480-754), ES = 5 dígitos (08201)."""
    if re.search(r"\b\d{4}-\d{3}\b", address):
        return "PT"
    if re.search(r"\b\d{5}\b", address):
        return "ES"
    return None


def city_from_address(address: str) -> str | None:
    """'Ctra. de Terrassa, 76 , Sabadell, Sabadell' -> 'Sabadell'."""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None
    city = re.sub(r"^\d[\d\s-]*", "", parts[-1]).strip()  # remove código postal
    return city or None


def scrape_tournament(tournament_id: str, slug: str) -> dict:
    parser = TournamentDetailParser()
    parser.feed(fetch(f"{BASE_URL}/en/league/tournament/{tournament_id}/{slug}/view"))
    f = parser.fields
    details: dict = {}

    if f.get("date", {}).get("datetimes"):
        details["date"] = f["date"]["datetimes"][0][:10]
    schedule = f.get("schedule", {}).get("datetimes") or []
    if schedule and "T" in schedule[0]:
        details["time"] = schedule[0].split("T", 1)[1][:5]
    if f.get("venue", {}).get("text"):
        details["venue"] = f["venue"]["text"]
    if f.get("address", {}).get("text"):
        details["city"] = city_from_address(f["address"]["text"])
    # País: 1) código no fim do link do mapa; 2) formato do código postal no endereço
    for href in f.get("map", {}).get("hrefs", []):
        country = country_from_map_link(href)
        if country:
            details["country"] = country
            break
    if "country" not in details and f.get("address", {}).get("text"):
        country = country_from_address(f["address"]["text"])
        if country:
            details["country"] = country
    fmt = f.get("format", {}).get("text")
    if fmt:
        details["type"] = FORMAT_TYPES.get(fmt.lower(), fmt)
    if f.get("status", {}).get("text"):
        details["status"] = f["status"]["text"]
    if f.get("region", {}).get("text"):
        details["league"] = f["region"]["text"]
    return details


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"vtes.es -> {EVENTS_FILE}")
    print(f"Temporada: {SEASON_URL}")

    payload = load_payload(EVENTS_FILE)
    vekn_events = [e for e in payload["events"] if event_source(e) == SOURCE_VEKN]
    previous_ours = {e["id"]: e for e in payload["events"] if event_source(e) == SOURCE}
    other_events = [e for e in payload["events"] if event_source(e) not in (SOURCE_VEKN, SOURCE)]

    if not vekn_events:
        print("  aviso: events.json não tem eventos VEKN; corra antes o update_vekn_events.py "
              "para a deduplicação funcionar.", file=sys.stderr)

    # 1) Listas de torneios
    tournaments: dict[str, dict] = {}
    for url in LIST_URLS:
        try:
            items = scrape_list(url)
        except RuntimeError as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            print("events.json NÃO foi alterado.", file=sys.stderr)
            return 1
        print(f"  lista {url.rsplit('/', 1)[-1]}: {len(items)} torneio(s)")
        for item in items:
            tournaments.setdefault(item["id"], item)

    if not tournaments:
        print("ERRO: nenhum torneio encontrado. O layout do vtes.es pode ter mudado; "
              "events.json NÃO foi alterado.", file=sys.stderr)
        return 1

    # 2) Detalhes + deduplicação com a VEKN
    print(f"A obter detalhes de {len(tournaments)} torneio(s)...")
    added: list[dict] = []
    duplicates: list[tuple[dict, dict]] = []
    cancelled = 0
    detail_failures = 0

    for item in sorted(tournaments.values(), key=lambda t: t["date"] or ""):
        event_id = f"{SOURCE}-{item['id']}"
        try:
            details = scrape_tournament(item["id"], item["slug"])
        except RuntimeError as exc:
            detail_failures += 1
            print(f"  aviso: {exc}", file=sys.stderr)
            old = previous_ours.get(event_id, {})
            details = {k: old[k] for k in ("time", "venue", "country", "type", "league") if old.get(k)}
            if old.get("location") and "," in old["location"]:
                details["city"] = old["location"].split(",")[0].strip()

        status = details.get("status") or item["status"] or ""
        if "cancel" in status.lower():
            cancelled += 1
            continue

        country = details.get("country") or DEFAULT_COUNTRY
        country_name = COUNTRY_NAMES.get(country, country)
        city = details.get("city")
        record = {
            "id": event_id,
            "title": smart_title(item["title"]),
            "date": details.get("date") or item["date"],
            "time": details.get("time"),
            "country": country,
            "type": details.get("type"),
            "league": details.get("league") or item["league"],
            "venue": details.get("venue"),
            "location": f"{city}, {country_name}" if city else country_name,
            "link": f"{BASE_URL}/en/league/tournament/{item['id']}/{item['slug']}/view",
            "source": SOURCE,
        }
        if not record["date"]:
            continue

        match = next((v for v in vekn_events if is_same_event(record, v)), None)
        if match:
            duplicates.append((record, match))
        else:
            added.append(record)

    # 3) Grava: VEKN + outras fontes + vtes.es novos (os vtes.es antigos são substituídos)
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    sources[SOURCE] = SEASON_URL
    payload.pop("source", None)  # chave do formato antigo
    payload["sources"] = sources
    payload["generated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    payload["events"] = sort_events(vekn_events + other_events + added)
    write_json_atomic(EVENTS_FILE, payload)

    # 4) Resumo
    new_ids = {e["id"] for e in added} - set(previous_ours)
    removed_ids = set(previous_ours) - {e["id"] for e in added}
    print()
    print(f"OK: {len(added)} torneio(s) vtes.es acrescentados a {EVENTS_FILE}")
    print(f"  já existiam na VEKN (ignorados): {len(duplicates)}"
          + (f" | cancelados: {cancelled}" if cancelled else "")
          + (f" | detalhes em falta: {detail_failures}" if detail_failures else ""))
    print(f"  novos desde a última execução: {len(new_ids)} | removidos: {len(removed_ids)}")
    for record, match in duplicates:
        print(f"    = {record['date']} {record['title']}  =>  VEKN {match['id']} {match['title']}")
    for record in added:
        print(f"    + {record['date']} [{record['country']}] {record['title']} ({record['location']})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido; events.json NÃO foi alterado.", file=sys.stderr)
        sys.exit(130)

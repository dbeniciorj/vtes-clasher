#!/usr/bin/env python3
"""
update_vekn_events.py

Atualiza ../vtes-sim-site/events.json com os eventos do calendário oficial
da VEKN (https://www.vekn.net/event-calendar) para os países e meses
configurados abaixo.

Uso (sem parâmetros):
    python3 automation-jobs/update_vekn_events.py

Só usa a biblioteca padrão do Python 3.8+ (não precisa de pip install).

Comportamento:
  * Lê o calendário mensal de cada país: MONTHS_BACK meses anteriores,
    o mês atual e os MONTHS_AHEAD-1 meses seguintes.
  * Abre a página de cada evento para obter hora, local e cidade.
  * Se alguma página do calendário falhar, NÃO altera o events.json
    (evita publicar um calendário incompleto) e sai com código 1.
  * Se a página de detalhe de um evento falhar, reaproveita os dados da
    execução anterior (ou usa o nome do país como local).
  * Preserva os eventos de outras fontes (ex.: vtes.es) que já estejam no
    ficheiro, exceto os que passaram a existir na VEKN (a VEKN tem prioridade).
  * Grava o ficheiro de forma atómica (escreve num temporário e renomeia).
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # não criar __pycache__ ao importar events_common

import datetime as dt  # noqa: E402
import re  # noqa: E402
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

COUNTRIES = ["PT", "ES", "US", "BR"]
MONTHS_BACK = 2   # meses anteriores ao atual (em novembro: setembro e outubro)
MONTHS_AHEAD = 6  # mês atual + 5 seguintes

BASE_URL = "https://www.vekn.net"
CALENDAR_URL = BASE_URL + "/event-calendar?country={country}&date={year}-{month:02d}"
EVENT_URL = BASE_URL + "/event-calendar/event/{event_id}"

OUTPUT_FILE = EVENTS_FILE

EVENT_HREF_RE = re.compile(r"/event-calendar/event/(\d+)")
TITLE_COUNTRY_RE = re.compile(r"^(.*?)\s*\(([A-Z]{2})\)$")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")


# ---------------------------------------------------------------------------
# Parser do calendário mensal
# ---------------------------------------------------------------------------


def _classes(attrs: list[tuple[str, str | None]]) -> list[str]:
    return (dict(attrs).get("class") or "").split()


class CalendarParser(HTMLParser):
    """
    Lê a tabela <table class="vekn-cal">. Cada semana é um <tr class="cal-week">
    e cada dia um <td class="cal-this"> (mês pedido) ou <td class="cal-off">
    (dias do mês anterior/seguinte, que também podem ter eventos).

    Resultado em self.cells: lista de dicts
        {"week": int, "off": bool, "day": int|None, "events": [...]}
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict] = []
        self._in_cal = False
        self._table_depth = 0
        self._week = -1
        self._in_week_row = False
        self._cell: dict | None = None
        self._in_h2 = False
        self._h2_text = ""
        self._anchor: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._in_cal:
                self._table_depth += 1
            elif "vekn-cal" in _classes(attrs):
                self._in_cal = True
                self._table_depth = 1
            return

        if not self._in_cal:
            return

        if tag == "tr":
            self._in_week_row = "cal-week" in _classes(attrs)
            if self._in_week_row:
                self._week += 1
        elif tag == "td" and self._in_week_row:
            classes = _classes(attrs)
            if "cal-this" in classes or "cal-off" in classes:
                self._cell = {
                    "week": self._week,
                    "off": "cal-off" in classes,
                    "day": None,
                    "events": [],
                }
        elif tag == "h2" and self._cell is not None:
            self._in_h2 = True
            self._h2_text = ""
        elif tag == "a" and self._cell is not None:
            match = EVENT_HREF_RE.search(dict(attrs).get("href") or "")
            if match:
                self._anchor = {
                    "id": match.group(1),
                    "type": (dict(attrs).get("title") or "").strip(),
                    "text": "",
                }

    def handle_endtag(self, tag):
        if not self._in_cal:
            return

        if tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_cal = False
        elif tag == "h2" and self._in_h2:
            self._in_h2 = False
            if self._cell is not None and self._h2_text.strip().isdigit():
                self._cell["day"] = int(self._h2_text.strip())
        elif tag == "a" and self._anchor is not None:
            text = " ".join(self._anchor["text"].split())
            match = TITLE_COUNTRY_RE.match(text)
            title, country = (match.group(1), match.group(2)) if match else (text, None)
            if self._cell is not None and title:
                self._cell["events"].append(
                    {
                        "id": self._anchor["id"],
                        "title": title,
                        "type": self._anchor["type"],
                        "country": country,
                    }
                )
            self._anchor = None
        elif tag == "td" and self._cell is not None:
            self.cells.append(self._cell)
            self._cell = None
        elif tag == "tr":
            self._in_week_row = False

    def handle_data(self, data):
        if self._in_h2:
            self._h2_text += data
        if self._anchor is not None:
            self._anchor["text"] += data


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def cell_date(year: int, month: int, cell: dict) -> dt.date | None:
    """Data real de uma célula; cal-off na 1.ª semana = mês anterior, senão = seguinte."""
    if cell["day"] is None:
        return None
    if cell["off"]:
        year, month = shift_month(year, month, -1 if cell["week"] == 0 else 1)
    try:
        return dt.date(year, month, cell["day"])
    except ValueError:
        return None


def scrape_calendar(country: str, year: int, month: int) -> list[dict]:
    html_text = fetch(CALENDAR_URL.format(country=country, year=year, month=month))
    parser = CalendarParser()
    parser.feed(html_text)

    if not parser.cells:
        raise RuntimeError(
            f"calendário {country} {year}-{month:02d} sem tabela 'vekn-cal' "
            "(o layout do site pode ter mudado)"
        )

    events = []
    for cell in parser.cells:
        date = cell_date(year, month, cell)
        if date is None:
            continue
        for ev in cell["events"]:
            events.append({**ev, "country": ev["country"] or country, "date": date})
    return events


# ---------------------------------------------------------------------------
# Parser da página de detalhe do evento
# ---------------------------------------------------------------------------


class EventDetailParser(HTMLParser):
    """Extrai o texto de <div class="eventdate"> e <div class="eventvenue">."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.date_text = ""
        self.venue_text = ""
        self._capture: str | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if self._capture:
            if tag == "div":
                self._depth += 1
            elif tag == "br" and self._capture == "venue":
                self.venue_text += "\n"
            return
        if tag == "div":
            classes = _classes(attrs)
            if "eventdate" in classes:
                self._capture, self._depth = "date", 1
            elif "eventvenue" in classes:
                self._capture, self._depth = "venue", 1

    def handle_endtag(self, tag):
        if self._capture and tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._capture = None

    def handle_data(self, data):
        if self._capture == "date":
            self.date_text += data
        elif self._capture == "venue":
            self.venue_text += data


def parse_location(address_line: str) -> str:
    """'08201, Sabadell, Catalunya, Spain' -> 'Sabadell, Catalunya, Spain'."""
    parts = [p.strip() for p in address_line.split(",") if p.strip()]
    if len(parts) > 1 and any(ch.isdigit() for ch in parts[0]):
        parts = parts[1:]  # remove o código postal
    return ", ".join(parts)


def pick_address_line(lines: list[str]) -> str | None:
    """
    A linha 'CEP, Cidade, Estado, País' costuma ser a última, mas às vezes
    vem seguida de um link do site da loja. Preferimos a linha que termina
    com o nome do país; senão, a última com vírgula que não seja um URL.
    """
    countries = tuple(name.lower() for name in COUNTRY_NAMES.values())
    candidates = [l for l in lines if "," in l and not l.lower().startswith(("http://", "https://", "www."))]
    for line in reversed(candidates):
        if line.lower().endswith(countries):
            return line
    return candidates[-1] if candidates else None


def scrape_event_details(event_id: str) -> dict:
    parser = EventDetailParser()
    parser.feed(fetch(EVENT_URL.format(event_id=event_id)))

    details: dict = {}

    time_match = TIME_RE.search(parser.date_text)
    if time_match:
        details["time"] = time_match.group(1).zfill(5)

    lines = [" ".join(l.split()) for l in parser.venue_text.split("\n")]
    lines = [l for l in lines if l]
    if lines:
        details["venue"] = lines[0]
        address = pick_address_line(lines[1:])
        if address:
            details["location"] = parse_location(address)

    return details


# ---------------------------------------------------------------------------
# Ficheiro de saída
# ---------------------------------------------------------------------------


def split_previous(payload: dict) -> tuple[dict[str, dict], list[dict]]:
    """Separa os eventos já gravados em (VEKN por id, eventos de outras fontes)."""
    vekn: dict[str, dict] = {}
    others: list[dict] = []
    for ev in payload["events"]:
        if event_source(ev) == SOURCE_VEKN:
            vekn[str(ev["id"])] = ev
        else:
            others.append(ev)
    return vekn, others


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    today = dt.date.today()
    months = [shift_month(today.year, today.month, i) for i in range(-MONTHS_BACK, MONTHS_AHEAD)]
    range_start = dt.date(months[0][0], months[0][1], 1)
    end_year, end_month = shift_month(months[-1][0], months[-1][1], 1)
    range_end = dt.date(end_year, end_month, 1)  # exclusivo

    print(f"VEKN -> {OUTPUT_FILE}")
    print(f"Países: {', '.join(COUNTRIES)} | Meses: {months[0][0]}-{months[0][1]:02d} "
          f"a {months[-1][0]}-{months[-1][1]:02d}")

    existing = load_payload(OUTPUT_FILE)
    previous, other_sources = split_previous(existing)

    # 1) Calendários mensais
    found: dict[str, dict] = {}
    for country in COUNTRIES:
        for year, month in months:
            label = f"{country} {year}-{month:02d}"
            try:
                events = scrape_calendar(country, year, month)
            except RuntimeError as exc:
                print(f"ERRO: {exc}", file=sys.stderr)
                print("events.json NÃO foi alterado.", file=sys.stderr)
                return 1
            kept = 0
            for ev in events:
                if ev["country"] not in COUNTRIES:
                    continue
                if not (range_start <= ev["date"] < range_end):
                    continue
                current = found.get(ev["id"])
                if current is None or ev["date"] < current["date"]:
                    found[ev["id"]] = ev
                if (ev["date"].year, ev["date"].month) == (year, month):
                    kept += 1  # conta só os do próprio mês (sem as células cal-off)
            print(f"  calendário {label}: {kept} evento(s)")

    if not found:
        print("ERRO: nenhum evento encontrado em nenhum país/mês. "
              "O layout do site pode ter mudado; events.json NÃO foi alterado.",
              file=sys.stderr)
        return 1

    # 2) Detalhes de cada evento
    print(f"A obter detalhes de {len(found)} evento(s)...")
    output_events = []
    detail_failures = 0
    for i, (event_id, ev) in enumerate(sorted(found.items(), key=lambda kv: kv[1]["date"]), 1):
        try:
            details = scrape_event_details(event_id)
        except RuntimeError as exc:
            detail_failures += 1
            print(f"  aviso: {exc}", file=sys.stderr)
            old = previous.get(event_id, {})
            details = {k: old[k] for k in ("time", "venue", "location") if old.get(k)}

        record = {
            "id": event_id,
            "title": ev["title"],
            "date": ev["date"].isoformat(),
            "time": details.get("time"),
            "country": ev["country"],
            "type": ev["type"] or None,
            "venue": details.get("venue"),
            "location": details.get("location") or COUNTRY_NAMES.get(ev["country"], ev["country"]),
            "link": EVENT_URL.format(event_id=event_id),
            "source": SOURCE_VEKN,
        }
        output_events.append(record)
        if i % 10 == 0:
            print(f"  {i}/{len(found)}")

    # 3) Eventos de outras fontes: mantém, exceto os que agora existem na VEKN
    kept_others = []
    replaced_by_vekn = []
    for ev in other_sources:
        match = next((v for v in output_events if is_same_event(ev, v)), None)
        if match:
            replaced_by_vekn.append((ev, match))
        else:
            kept_others.append(ev)

    sources = existing.get("sources") if isinstance(existing.get("sources"), dict) else {}
    sources[SOURCE_VEKN] = BASE_URL + "/event-calendar"

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "sources": sources,
        "countries": COUNTRIES,
        "months": [f"{y}-{m:02d}" for y, m in months],
        "events": sort_events(output_events + kept_others),
    }
    write_json_atomic(OUTPUT_FILE, payload)

    # 4) Resumo
    new_ids = set(found) - set(previous)
    removed_ids = set(previous) - set(found)
    per_country = {c: sum(1 for e in output_events if e["country"] == c) for c in COUNTRIES}
    print()
    print(f"OK: {len(output_events)} evento(s) VEKN gravados em {OUTPUT_FILE}")
    print("  por país: " + ", ".join(f"{c}={n}" for c, n in per_country.items()))
    print(f"  novos: {len(new_ids)} | removidos: {len(removed_ids)}"
          + (f" | detalhes em falta: {detail_failures}" if detail_failures else ""))
    if other_sources:
        print(f"  outras fontes preservadas: {len(kept_others)}"
              f" | substituídas por evento VEKN: {len(replaced_by_vekn)}")
        for ev, match in replaced_by_vekn:
            print(f"    - [{event_source(ev)}] {ev['date']} {ev['title']}  =>  VEKN {match['id']} {match['title']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido; events.json NÃO foi alterado.", file=sys.stderr)
        sys.exit(130)

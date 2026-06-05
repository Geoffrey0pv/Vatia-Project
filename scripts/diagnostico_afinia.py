from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


URL_AFINIA = "https://afinia.com.co/inicio/tarifas-y-subsidios"
KEYWORDS = [
    "pdf",
    "tarifa",
    "tarifas",
    "subsidio",
    "subsidios",
    "costo",
    "unitario",
    "energia",
    "energía",
    "cu",
]
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@dataclass
class LinkRecord:
    source: str
    text: str
    href: str
    absolute_url: str
    resource_type: str
    seems_monthly: bool
    cycle: str | None


def detect_resource_type(url: str) -> str:
    lower = unquote(url).lower()
    if ".pdf" in lower:
        return "pdf"
    if ".xlsx" in lower or ".xls" in lower:
        return "excel"
    if ".csv" in lower:
        return "csv"
    if ".json" in lower:
        return "json"
    return "html"


def detect_cycle(text: str) -> str | None:
    lower = unquote(text).lower()

    match_yyyymm = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])\b", lower)
    if match_yyyymm:
        return f"{match_yyyymm.group(1)}{match_yyyymm.group(2)}"

    match_date = re.search(r"(\d{1,2})[-_/](\d{1,2})[-_/](20\d{2})", lower)
    if match_date:
        _day, month, year = match_date.groups()
        return f"{year}{int(month):02d}"

    months = {
        "enero": "01",
        "febrero": "02",
        "marzo": "03",
        "abril": "04",
        "mayo": "05",
        "junio": "06",
        "julio": "07",
        "agosto": "08",
        "septiembre": "09",
        "setiembre": "09",
        "octubre": "10",
        "noviembre": "11",
        "diciembre": "12",
    }
    year_match = re.findall(r"(20\d{2})", lower)
    month_num = next((num for name, num in months.items() if name in lower), None)
    if year_match and month_num:
        return f"{year_match[-1]}{month_num}"

    return None


def seems_monthly(text: str) -> bool:
    lower = unquote(text).lower()
    return bool(detect_cycle(lower)) or any(
        token in lower
        for token in ["mensual", "mes", "tarifa", "subsidio", "costo unitario"]
    )


def looks_relevant(text: str, href: str) -> bool:
    blob = f"{text} {href}".lower()
    return any(keyword in blob for keyword in KEYWORDS)


def normalize_link(source: str, text: str, href: str, base_url: str) -> LinkRecord:
    absolute = urljoin(base_url, href.strip())
    joined_text = " ".join(text.split())
    material = f"{joined_text} {absolute}"
    return LinkRecord(
        source=source,
        text=joined_text,
        href=href.strip(),
        absolute_url=absolute,
        resource_type=detect_resource_type(absolute),
        seems_monthly=seems_monthly(material),
        cycle=detect_cycle(material),
    )


def parse_links_from_html(html: str, source: str, base_url: str) -> list[LinkRecord]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[LinkRecord] = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "").strip()
        text = a_tag.get_text(" ", strip=True)
        if not href:
            continue
        if not looks_relevant(text, href):
            continue
        records.append(normalize_link(source, text, href, base_url))
    return records


def request_page(url: str, timeout: int = 30) -> dict:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    body = response.text
    return {
        "ok": response.ok,
        "status_code": response.status_code,
        "final_url": response.url,
        "content_type": response.headers.get("Content-Type"),
        "html_length": len(body),
        "html": body,
        "links": parse_links_from_html(body, "requests", response.url),
    }


def maybe_playwright(url: str, timeout_ms: int = 30000) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional dependency
        return {
            "available": False,
            "error": f"Playwright no disponible: {exc}",
            "links": [],
            "network": [],
        }

    results: dict = {
        "available": True,
        "error": None,
        "links": [],
        "network": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
        seen_network: set[str] = set()

        def record_response(resp) -> None:
            resource_url = resp.url
            if resource_url in seen_network:
                return
            seen_network.add(resource_url)
            blob = resource_url.lower()
            if any(keyword in blob for keyword in KEYWORDS) or any(
                ext in blob for ext in [".pdf", ".xlsx", ".xls", ".json", "/api/", "xhr"]
            ):
                content_type = resp.headers.get("content-type", "")
                results["network"].append(
                    {
                        "url": resource_url,
                        "status": resp.status,
                        "resource_type": resp.request.resource_type,
                        "content_type": content_type,
                    }
                )

        page.on("response", record_response)
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        html = page.content()
        results["title"] = page.title()
        results["final_url"] = page.url
        results["html_length"] = len(html)
        results["links"] = parse_links_from_html(html, "playwright", page.url)
        browser.close()
    return results


def dedupe_links(records: Iterable[LinkRecord]) -> list[LinkRecord]:
    unique: dict[tuple[str, str], LinkRecord] = {}
    for record in records:
        key = (record.absolute_url, record.source)
        unique[key] = record
    return sorted(
        unique.values(),
        key=lambda r: (
            r.resource_type != "pdf",
            not r.seems_monthly,
            r.cycle or "",
            r.absolute_url,
        ),
    )


def format_table(records: list[LinkRecord]) -> str:
    headers = ["source", "text", "href", "type", "monthly", "cycle"]
    rows = []
    for record in records:
        rows.append(
            [
                record.source,
                record.text[:80],
                record.absolute_url[:120],
                record.resource_type,
                "yes" if record.seems_monthly else "no",
                record.cycle or "",
            ]
        )

    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    lines = [
        " | ".join(str(h).ljust(widths[idx]) for idx, h in enumerate(headers)),
        "-+-".join("-" * widths[idx] for idx in range(len(headers))),
    ]
    for row in rows:
        lines.append(
            " | ".join(str(cell).ljust(widths[idx]) for idx, cell in enumerate(row))
        )
    return "\n".join(lines)


def save_json(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico técnico de fuente Afinia")
    parser.add_argument("--url", default=URL_AFINIA)
    parser.add_argument(
        "--json-out",
        default="data/diagnostics/afinia_diagnostico.json",
        help="Ruta para guardar el reporte JSON.",
    )
    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="Omitir intento con Playwright.",
    )
    args = parser.parse_args()

    report: dict[str, object] = {
        "target_url": args.url,
        "keywords": KEYWORDS,
    }

    print(f"[diagnostico] URL objetivo: {args.url}")

    try:
        request_result = request_page(args.url)
        report["requests"] = {
            k: v for k, v in request_result.items() if k != "html" and k != "links"
        }
        request_links = dedupe_links(request_result["links"])
        report["requests"]["links"] = [asdict(r) for r in request_links]
        print(
            f"[requests] status={request_result['status_code']} "
            f"final={request_result['final_url']} html={request_result['html_length']}"
        )
        print(f"[requests] links relevantes: {len(request_links)}")
    except Exception as exc:
        report["requests"] = {"error": str(exc)}
        request_links = []
        print(f"[requests] error: {exc}")

    playwright_links: list[LinkRecord] = []
    if not args.no_playwright:
        try:
            playwright_result = maybe_playwright(args.url)
            network = playwright_result.pop("network", [])
            links = playwright_result.pop("links", [])
            playwright_links = dedupe_links(links)
            report["playwright"] = playwright_result
            report["playwright"]["links"] = [asdict(r) for r in playwright_links]
            report["playwright"]["network"] = network
            if playwright_result.get("available"):
                print(
                    f"[playwright] final={playwright_result.get('final_url')} "
                    f"html={playwright_result.get('html_length')} "
                    f"links relevantes={len(playwright_links)}"
                )
                print(f"[playwright] network eventos relevantes: {len(network)}")
            else:
                print(f"[playwright] {playwright_result.get('error')}")
        except Exception as exc:
            report["playwright"] = {"error": str(exc)}
            print(f"[playwright] error: {exc}")

    all_links = dedupe_links([*request_links, *playwright_links])
    report["all_links"] = [asdict(r) for r in all_links]

    print("\n=== ENLACES RELEVANTES ===")
    if all_links:
        print(format_table(all_links))
    else:
        print("No se detectaron enlaces relevantes con las estrategias ejecutadas.")

    if report.get("playwright", {}).get("network"):
        print("\n=== NETWORK RELEVANTE (Playwright) ===")
        for item in report["playwright"]["network"]:
            print(
                f"{item['status']} | {item['resource_type']} | "
                f"{item['content_type']} | {item['url']}"
            )

    save_json(Path(args.json_out), report)
    print(f"\n[diagnostico] JSON guardado en: {args.json_out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Atualiza o feed do Radar V3 usando RSS público do Google Notícias.

Somente manchetes, fontes, links e datas são extraídos do RSS. O sistema
não inventa resumos jornalísticos ou declarações atribuídas às fontes.
Sem bibliotecas externas, API paga, credenciais ou backend permanente.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "noticias.json"
UTC = timezone.utc
MAX_PER_NICHE = 12
MAX_AGE_DAYS = 21

# Pautas pensadas para negócios brasileiros. Quando uma busca falha, a última
# edição válida daquele nicho permanece disponível (com data anterior).
SEARCHES = {
    "food": [
        ("restaurantes delivery bares alimentação negócios Brasil when:7d", "Mercado"),
        ("iFood Keeta Rappi delivery restaurantes Brasil when:7d", "Delivery"),
    ],
    "beauty": [
        ("mercado de beleza salão estética negócios Brasil when:7d", "Mercado"),
        ("tendências cosméticos serviços beleza empreendedores Brasil when:7d", "Tendências"),
    ],
    "car": [
        ("oficinas automotivas reparação veículos Brasil setor when:7d", "Mercado"),
        ("tecnologia mecânica automotiva manutenção carros Brasil when:7d", "Ferramentas"),
    ],
    "shop": [
        ("varejo pequenos negócios comércio Brasil vendas when:7d", "Mercado"),
        ("comércio eletrônico varejo marketplace lojistas Brasil when:7d", "Oportunidades"),
    ],
    "tech": [
        ("tecnologia inteligência artificial empresas Brasil novidades when:7d", "Tendências"),
        ("software SaaS aplicativos automação negócios Brasil when:7d", "Ferramentas"),
    ],
    "health": [
        ("academias fitness bem estar mercado Brasil negócios when:7d", "Mercado"),
        ("tendências academias atividade física bem estar Brasil when:7d", "Tendências"),
    ],
}

GUIDANCE = {
    "food": (
        "Observe se o tema interfere em custos, entregas, demanda ou concorrência na sua região. Confira a matéria antes de ajustar sua operação.",
        "Compare essa novidade com seus números de vendas, custos e canais de delivery antes de tomar uma decisão.",
        "Avalie uma possível consequência para sua operação de alimentação."),
    "beauty": (
        "Procure impactos possíveis nos serviços, agendamentos, produtos e expectativas de clientes da sua região.",
        "Converse com clientes ou equipe sobre a relevância dessa novidade antes de adotar uma mudança.",
        "Investigue se o assunto é relevante para seu salão ou negócio de beleza."),
    "car": (
        "Verifique se a notícia afeta peças, tecnologias, preços ou demanda dos serviços da sua oficina.",
        "Confira a aplicabilidade aos veículos que você atende antes de investir em novas soluções.",
        "Reflita sobre possíveis mudanças na sua operação automotiva."),
    "shop": (
        "Reflita sobre custos, preferência do consumidor, concorrência e oportunidades para sua loja.",
        "Teste a relevância da notícia com dados do seu estoque, fluxo de clientes e margem.",
        "Explore uma possível oportunidade para seu negócio de comércio."),
    "tech": (
        "Considere efeitos possíveis para ferramentas, desenvolvimento, produtividade ou clientes da sua empresa.",
        "Avalie um pequeno teste e defina como medir o resultado antes de adotar a novidade.",
        "Acompanhe uma tendência e sua possível utilidade para empresas."),
    "health": (
        "Examine como o tema pode interessar ao público e aos serviços da sua operação de bem-estar.",
        "Converse com sua equipe e seus clientes antes de mudar serviços ou comunicação.",
        "Considere uma tendência potencialmente relevante para seu público."),
}


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    return re.sub(r"[^a-z0-9]+", "-", "".join(c for c in folded if not unicodedata.combining(c)).lower()).strip("-")


def rss_url(q: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": q, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt"}
    )


def parse_feed(xml_bytes: bytes, niche: str, category: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    results = []
    for entry in root.findall(".//channel/item")[:25]:
        title = clean_spaces(entry.findtext("title", ""))
        href = clean_spaces(entry.findtext("link", ""))
        publisher = clean_spaces(entry.findtext("source", "")) or "Google Notícias"
        raw_date = clean_spaces(entry.findtext("pubDate", ""))
        if not title or not href or not raw_date or urlparse(href).scheme != "https":
            continue
        try:
            date = parsedate_to_datetime(raw_date)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            date = date.astimezone(UTC)
        except (ValueError, TypeError, IndexError):
            continue
        if date < datetime.now(UTC) - timedelta(days=MAX_AGE_DAYS) or date > datetime.now(UTC) + timedelta(days=1):
            continue
        # Frequentemente o título traz ' - Nome do veículo'; retire só se coincide com a fonte RSS.
        suffix = " - " + publisher
        if title.lower().endswith(suffix.lower()):
            title = title[:-len(suffix)].strip()
        if len(title) < 12 or len(title) > 240:
            continue
        key = hashlib.sha256((niche + "|" + normalized(title)).encode("utf-8")).hexdigest()[:24]
        why, action, impact = GUIDANCE[niche]
        results.append({
            "id": f"rss-{niche}-{key}",
            "n": niche,
            "title": title,
            "summary": f"Manchete divulgada por {publisher}. Abra a publicação original para conferir os detalhes e a contextualização da notícia.",
            "why": why,
            "action": action,
            "impact": impact,
            "type": category,
            "source": publisher[:100],
            "publishedAt": date.isoformat(timespec="seconds"),
            "url": href,
            "auto": True,
        })
    return results


def fetch_items(niche: str, query: str, category: str) -> list[dict]:
    req = Request(rss_url(query), headers={
        "User-Agent": "Mozilla/5.0 (compatible; RadarEmpreendedor/3.0; RSS reader)",
        "Accept": "application/rss+xml,application/xml,text/xml",
    })
    with urlopen(req, timeout=15) as reply:
        data = reply.read(1_500_000)
    return parse_feed(data, niche, category)


def old_feed() -> dict:
    try:
        old = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return old if isinstance(old, dict) and isinstance(old.get("articles"), list) else {}
    except (OSError, ValueError):
        return {}


def build_news(fetcher=fetch_items, base: dict | None = None, now: datetime | None = None) -> tuple[dict, dict]:
    previous = base if base is not None else old_feed()
    now = now or datetime.now(UTC)
    previous_dates = previous.get("nicheCheckedAt") or {}
    niche_dates = dict(previous_dates)
    article_pool = []
    report = {}
    any_success = False
    for niche, queries in SEARCHES.items():
        fetched = []
        successful = 0
        errors = []
        for query, category in queries:
            try:
                fetched.extend(fetcher(niche, query, category))
                successful += 1
                any_success = True
            except Exception as exc:  # Uma fonte fora do ar não apaga a edição anterior.
                errors.append(f"{type(exc).__name__}: {str(exc)[:100]}")
            time.sleep(0.15)
        if fetched:
            unique = {}
            for article in sorted(fetched, key=lambda x: x["publishedAt"], reverse=True):
                unique.setdefault(article["id"], article)
            picked = list(unique.values())[:MAX_PER_NICHE]
            niche_dates[niche] = now.isoformat(timespec="seconds")
            status = "updated"
        else:
            picked = [a for a in previous.get("articles", []) if a.get("n", "food") == niche][:MAX_PER_NICHE]
            status = "kept-last-edition" if picked else "no-articles"
        article_pool.extend(picked)
        report[niche] = {"status": status, "items": len(picked), "queries_ok": successful, "errors": errors}
    # Preserve editorial material when all external feeds are unavailable.
    if not any_success:
        return previous, {"allSourcesFailed": True, "niches": report}
    return {
        "version": 3,
        "revision": int(previous.get("revision", 0)) + 1,
        "checkedAt": now.isoformat(timespec="seconds"),
        "nicheCheckedAt": niche_dates,
        "mode": "scheduled-rss",
        "articles": article_pool,
    }, {"allSourcesFailed": False, "niches": report}


def main() -> int:
    new_feed, report = build_news()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["allSourcesFailed"]:
        print("ERRO: nenhuma fonte respondeu. Edição anterior preservada.", file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(json.dumps(new_feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(OUTPUT)
    print(f"Feed gerado: {len(new_feed['articles'])} notícias, edição {new_feed['revision']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


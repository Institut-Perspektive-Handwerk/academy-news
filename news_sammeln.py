"""Sammelt News aus Handwerk und Bau für den Nachrichtenstreifen der IPH Academy.

Liest die Feeds aus quellen.json, ergänzt die eigenen Beiträge aus eigene.json (IPH-Artikel, Podcast, Academy-News)
und schreibt news.json: nur Titel, Link, Quelle, Datum, Bereich - kein Fließtext der Verlage (nur Überschrift + Link).

Mit ANTHROPIC_API_KEY in der Umgebung ordnet Claude jede neue Meldung ein (relevant?, Gewerke, Rollen, Thema, "Was heißt das",
"Was tust du", Frist; siehe einordnen.py). Ohne Schlüssel läuft der Streifen wie bisher ohne Einordnung.

Aufruf: python news_sammeln.py [--ziel news.json]
Läuft ohne Zusatzpakete (nur Standardbibliothek), gedacht für eine GitHub Action alle 2 Stunden.
"""
import argparse
import concurrent.futures as cf
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from einordnen import einordnen

HIER = Path(__file__).resolve().parent
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 IPH-Academy-News/1.0',
      'Accept-Language': 'de-DE'}
ATOM = '{http://www.w3.org/2005/Atom}'
DC = '{http://purl.org/dc/elements/1.1/}'


def datum_lesen(txt):
    txt = (txt or '').strip()
    if not txt:
        return None
    try:
        d = email.utils.parsedate_to_datetime(txt)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(txt.replace('Z', '+00:00'))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone(timedelta(hours=2)))
    return d.astimezone(timezone.utc)


def sauber(t):
    t = html.unescape(re.sub(r'<[^>]+>', '', t or ''))
    return re.sub(r'\s+', ' ', t).strip()


def feed_lesen(q):
    try:
        req = urllib.request.Request(q['url'], headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read(3_000_000))
    except Exception as e:  # eine kaputte Quelle darf den Lauf nicht stoppen
        return q, [], f'{type(e).__name__}: {e}'[:120]
    eintraege = []
    for it in root.findall('.//item') + root.findall(f'.//{ATOM}entry'):
        titel = sauber(it.findtext('title') or it.findtext(f'{ATOM}title'))
        link = (it.findtext('link') or '').strip()
        if not link:
            a = it.find(f'{ATOM}link')
            link = a.get('href', '') if a is not None else ''
        d = datum_lesen(it.findtext('pubDate') or it.findtext(f'{ATOM}updated') or it.findtext(f'{ATOM}published') or it.findtext(f'{DC}date'))
        link = urllib.parse.urljoin(q['url'], link) if link else ''
        kategorien = ' '.join((c.text or '') for c in it.findall('category'))
        if 'nicht im RSS' in kategorien:  # IKZ markiert so Beiträge, die nicht in Feeds gehören
            continue
        if not titel or not link.startswith('http'):
            continue
        filt = q.get('nur_mit')
        if filt and not re.search(filt, titel, re.I):
            continue
        anriss = sauber(it.findtext('description') or it.findtext(f'{ATOM}summary') or '')[:300]  # nur für die KI, nicht in news.json
        eintraege.append({'titel': titel, 'link': link, 'quelle': q['name'], 'bereich': q['bereich'],
                          'datum': d.isoformat() if d else None, 'anriss': anriss})
    return q, eintraege, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ziel', default=str(HIER / 'news.json'))
    args = ap.parse_args()
    cfg = json.loads((HIER / 'quellen.json').read_text(encoding='utf-8'))
    jetzt = datetime.now(timezone.utc)
    grenze = jetzt - timedelta(days=cfg.get('max_alter_tage', 14))
    alle, protokoll = [], []
    with cf.ThreadPoolExecutor(8) as ex:
        for q, eintraege, fehler in ex.map(feed_lesen, cfg['quellen']):
            frisch = [e for e in eintraege if e['datum'] and grenze <= datum_lesen(e['datum']) <= jetzt + timedelta(hours=6)]
            frisch.sort(key=lambda e: e['datum'], reverse=True)
            alle += frisch[:q.get('je_quelle', cfg.get('je_quelle', 5))]  # Quellen mit viel Fremdem (z. B. IBR) holen mehr
            protokoll.append(f"{q['name']}: {len(eintraege)} gelesen, {len(frisch)} frisch" + (f' - FEHLER {fehler}' if fehler else ''))
    # Quellen reihum mischen (sonst stehen 5 Meldungen eines Portals hintereinander), je Runde neueste zuerst
    je = {}
    for e in sorted(alle, key=lambda e: e['datum'], reverse=True):
        je.setdefault(e['quelle'], []).append(e)
    reihum = []
    while any(je.values()):
        runde = [v.pop(0) for v in je.values() if v]
        reihum += sorted(runde, key=lambda e: e['datum'], reverse=True)
    # doppelte Meldungen (gleicher Titel bei zwei Portalen) nur einmal
    gesehen, news = set(), []
    for e in reihum:
        k = re.sub(r'\W+', '', e['titel'].lower())[:60]
        if k not in gesehen:
            gesehen.add(k)
            news.append(e)
    # KI-Einordnung (nur neue Meldungen, Rest aus dem Zwischenspeicher)
    cache_pfad = HIER / 'einordnung.json'
    cache = json.loads(cache_pfad.read_text(encoding='utf-8')) if cache_pfad.exists() else {}
    schluessel = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if schluessel:
        neu = [dict(e, id=hashlib.sha1(e['link'].encode()).hexdigest()[:12]) for e in news if e['link'] not in cache]
        if neu:
            erg, verbrauch = einordnen(neu, cfg['gewerke'], cfg['rollen'], cfg['themen'], schluessel)
            for e in neu:
                if e['id'] in erg:
                    cache[e['link']] = dict(erg[e['id']], gesehen=jetzt.date().isoformat())
            print(f"Einordnung: {len(neu)} neu, {len(erg)} eingeordnet, {verbrauch['aufrufe']} Aufrufe, "
                  f"{verbrauch['eingabe']} Eingabe- / {verbrauch['ausgabe']} Ausgabe-Token")
        alt = (jetzt - timedelta(days=30)).date().isoformat()
        cache = {k: v for k, v in cache.items() if v.get('gesehen', '9999') >= alt}
        cache_pfad.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
    eingeordnet = []
    for e in news:
        e.pop('anriss', None)
        k = cache.get(e['link'])
        if k:
            if not k.get('relevant'):
                continue  # von der KI aussortiert (z. B. Personalien, PR ohne Nutzen)
            e.update({x: k.get(x) for x in ('gewerke', 'rollen', 'thema', 'was', 'tun', 'frist', 'gewicht')})
        eingeordnet.append(e)
    print(f'{len(news) - len(eingeordnet)} Meldungen als nicht relevant aussortiert')
    news = eingeordnet[:cfg.get('gesamt', 40)]
    eigene_pfad = HIER / 'eigene.json'
    eigene = json.loads(eigene_pfad.read_text(encoding='utf-8')) if eigene_pfad.exists() else []
    eigene = [dict(e, quelle=e.get('quelle', 'IPH'), bereich='IPH', thema='IPH', gewerke=e.get('gewerke', ['Alle Gewerke']),
                   rollen=e.get('rollen', cfg['rollen'])) for e in eigene if e.get('aktiv', True)]
    # eigene Beiträge gleichmäßig einstreuen: nach jeder 4. Fremdmeldung einer
    gemischt, i = [], 0
    for n, e in enumerate(news):
        gemischt.append(e)
        if eigene and (n + 1) % cfg.get('iph_alle', 4) == 0:
            gemischt.append(eigene[i % len(eigene)])
            i += 1
    if eigene and i == 0:
        gemischt = eigene + gemischt
    heute = jetzt.date().isoformat()
    fristen = sorted([e for e in news if e.get('frist') and e['frist'] >= heute], key=lambda e: e['frist'])[:5]
    aus = {'stand': jetzt.isoformat(timespec='minutes'), 'anzahl': len(gemischt), 'gewerke': cfg['gewerke'], 'rollen': cfg['rollen'],
           'fristen': [{k: e.get(k) for k in ('titel', 'link', 'quelle', 'frist', 'tun')} for e in fristen], 'news': gemischt}
    Path(args.ziel).write_text(json.dumps(aus, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n'.join(protokoll))
    print(f'{len(news)} Fremdmeldungen + {len(eigene)} eigene -> {args.ziel}')
    if not news:
        sys.exit(1)  # Action soll rot werden, wenn gar nichts kam


if __name__ == '__main__':
    main()

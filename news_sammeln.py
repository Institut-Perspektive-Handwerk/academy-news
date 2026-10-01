"""Sammelt News aus Handwerk und Bau für den Nachrichtenstreifen der IPH Academy.

Liest die Feeds aus quellen.json, ergänzt die eigenen Beiträge aus eigene.json (IPH-Artikel, Podcast, Academy-News)
und schreibt news.json: nur Titel, Link, Quelle, Datum, Bereich - kein Fließtext der Verlage (nur Überschrift + Link).

Aufruf: python news_sammeln.py [--ziel news.json]
Läuft ohne Zusatzpakete (nur Standardbibliothek), gedacht für eine GitHub Action alle 2 Stunden.
"""
import argparse
import concurrent.futures as cf
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        eintraege.append({'titel': titel, 'link': link, 'quelle': q['name'], 'bereich': q['bereich'],
                          'datum': d.isoformat() if d else None})
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
            alle += frisch[:cfg.get('je_quelle', 5)]
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
    news = news[:cfg.get('gesamt', 40)]
    eigene_pfad = HIER / 'eigene.json'
    eigene = json.loads(eigene_pfad.read_text(encoding='utf-8')) if eigene_pfad.exists() else []
    eigene = [dict(e, quelle=e.get('quelle', 'IPH'), bereich='IPH') for e in eigene if e.get('aktiv', True)]
    # eigene Beiträge gleichmäßig einstreuen: nach jeder 4. Fremdmeldung einer
    gemischt, i = [], 0
    for n, e in enumerate(news):
        gemischt.append(e)
        if eigene and (n + 1) % cfg.get('iph_alle', 4) == 0:
            gemischt.append(eigene[i % len(eigene)])
            i += 1
    if eigene and i == 0:
        gemischt = eigene + gemischt
    aus = {'stand': jetzt.isoformat(timespec='minutes'), 'anzahl': len(gemischt), 'news': gemischt}
    Path(args.ziel).write_text(json.dumps(aus, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n'.join(protokoll))
    print(f'{len(news)} Fremdmeldungen + {len(eigene)} eigene -> {args.ziel}')
    if not news:
        sys.exit(1)  # Action soll rot werden, wenn gar nichts kam


if __name__ == '__main__':
    main()

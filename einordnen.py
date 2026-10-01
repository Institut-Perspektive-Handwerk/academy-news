"""KI-Einordnung der Meldungen für Handwerksbetriebe (Claude, nur Standardbibliothek).

Die KI bekommt je Meldung nur Überschrift und Anrisstext (Feed), nie den Artikel. Sie entscheidet, ob die Meldung für
einen Handwerks- oder Baubetrieb relevant ist, für welche Gewerke und Rollen, zu welchem Thema, und schreibt zwei kurze
eigene Sätze: "Was heißt das für dich?" und "Was tust du jetzt?". Ergebnisse landen im Zwischenspeicher
(einordnung.json, Schlüssel = Link), damit jede Meldung nur einmal Geld kostet.
"""
import json
import os
import re
import urllib.request

MODELL = os.environ.get('NEWS_MODELL', 'claude-sonnet-5')
API = 'https://api.anthropic.com/v1/messages'

ANWEISUNG = """Du ordnest Branchenmeldungen für Handwerks- und Baubetriebe in Deutschland ein (5 bis 200 Mitarbeiter:
Inhaber, Projekt- und Bauleiter, Büro, Monteure, Ausbilder). Ton: direkt, auf Augenhöhe, Du-Form, kein Werbesprech.

Für jede Meldung entscheidest du:
- relevant: true nur, wenn ein Betrieb daraus etwas lernen, beachten oder tun kann (Recht, Pflichten, Fristen, Förderung,
  Preise/Lieferzeiten, Tarif, Arbeitsschutz, Normen, VOB/Urteile, Technik/Verfahren im Gewerk, Personal/Ausbildung, KI und
  Software im Betrieb, Markt/Konjunktur mit Folgen). false für: Personalien von Herstellern, Firmenjubiläen, Messe-Smalltalk,
  reine Produkt-PR ohne Nutzen, Ereignisse ohne Bezug zum Betrieb, Politik ohne Folgen für Betriebe.
- gewerke: Liste aus GEWERKE (genau diese Schreibweise); ["Alle Gewerke"], wenn es alle betrifft.
- rollen: Liste aus ROLLEN.
- thema: genau eins aus THEMEN.
- was: ein Satz, max. 140 Zeichen: Was heißt das für den Betrieb? Konkret, keine Wiederholung der Überschrift.
- tun: ein Satz, max. 100 Zeichen, mit Verb am Anfang (z. B. "Prüfe ...", "Sprich mit ..."); leer, wenn es nichts zu tun gibt.
- frist: Datum JJJJ-MM-TT nur für echte Stichtage mit Handlungsbedarf für Betriebe (Pflicht gilt ab, Antragsfrist,
  Meldefrist, Übergangsfrist endet), sonst null. Veranstaltungs-, Seminar- oder Messetermine sind KEINE Frist.
- gewicht: 1 (nett zu wissen), 2 (wichtig), 3 (muss jeder Betrieb wissen).
Erfinde keine Fakten, Zahlen oder Fristen. Nutze nur, was in Überschrift und Anriss steht.

Antworte NUR mit einem JSON-Array, ein Objekt je Meldung in der Eingabereihenfolge:
[{"id": "...", "relevant": true, "gewerke": [...], "rollen": [...], "thema": "...", "was": "...", "tun": "...", "frist": null, "gewicht": 2}]"""


def einordnen(meldungen, gewerke, rollen, themen, schluessel, paket=15, _tiefe=0):
    """meldungen: Liste von dicts mit id, titel, anriss, quelle. Gibt {id: einordnung}, verbrauch zurück.
    Kommt für ein Paket kein gültiges JSON zurück, wird es einmal in zwei Hälften neu versucht."""
    ergebnis, verbrauch = {}, {'eingabe': 0, 'ausgabe': 0, 'aufrufe': 0}

    def nachholen(teil):
        if _tiefe >= 1 or len(teil) < 2:
            return
        for h in (teil[:len(teil) // 2], teil[len(teil) // 2:]):
            e2, v2 = einordnen(h, gewerke, rollen, themen, schluessel, paket=len(h), _tiefe=_tiefe + 1)
            ergebnis.update(e2)
            for k in verbrauch:
                verbrauch[k] += v2[k]

    for i in range(0, len(meldungen), paket):
        teil = meldungen[i:i + paket]
        eingabe = 'GEWERKE: ' + json.dumps(gewerke, ensure_ascii=False) + '\nROLLEN: ' + json.dumps(rollen, ensure_ascii=False) \
            + '\nTHEMEN: ' + json.dumps(themen, ensure_ascii=False) + '\n\nMELDUNGEN:\n' \
            + json.dumps([{k: m[k] for k in ('id', 'titel', 'anriss', 'quelle')} for m in teil], ensure_ascii=False)
        body = json.dumps({'model': MODELL, 'max_tokens': 8000, 'system': ANWEISUNG,
                           'messages': [{'role': 'user', 'content': eingabe}]}).encode()
        req = urllib.request.Request(API, data=body, headers={'x-api-key': schluessel, 'anthropic-version': '2023-06-01',
                                                              'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                antwort = json.load(r)
        except Exception as e:  # Einordnung ist ein Zusatz: bei Fehlern läuft der Streifen ohne sie weiter
            print(f'Einordnung Paket {i // paket + 1} fehlgeschlagen: {type(e).__name__}: {str(e)[:120]}')
            continue
        verbrauch['aufrufe'] += 1
        verbrauch['eingabe'] += antwort.get('usage', {}).get('input_tokens', 0)
        verbrauch['ausgabe'] += antwort.get('usage', {}).get('output_tokens', 0)
        text = ''.join(b.get('text', '') for b in antwort.get('content', []) if b.get('type') == 'text')
        m = re.search(r'\[.*\]', text, re.S)
        try:
            liste = json.loads(m.group(0)) if m else []
        except json.JSONDecodeError:
            print(f'Einordnung Paket {i // paket + 1}: Antwort war kein gültiges JSON' + (' - neuer Versuch in zwei Hälften' if _tiefe == 0 else ''))
            nachholen(teil)
            continue
        for e in liste:
            if not isinstance(e, dict) or 'id' not in e:
                continue
            e['gewerke'] = [g for g in e.get('gewerke') or [] if g in gewerke] or ['Alle Gewerke']
            e['rollen'] = [r for r in e.get('rollen') or [] if r in rollen] or rollen[:]
            if e.get('thema') not in themen:
                e['thema'] = 'Markt'
            e['was'] = (e.get('was') or '')[:160]
            e['tun'] = (e.get('tun') or '')[:120]
            ergebnis[str(e['id'])] = e
    return ergebnis, verbrauch

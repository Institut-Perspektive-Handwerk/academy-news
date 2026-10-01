# academy-news

Sammelt alle 2 Stunden Nachrichten aus Handwerk und Bau für den Nachrichtenstreifen im Dashboard der IPH Academy.

- `quellen.json` – Feeds der Portale (Name, Bereich, Adresse, optional `nur_mit` als Stichwortfilter)
- `eigene.json` – eigene Beiträge (Artikel, Podcast, Academy-News); `aktiv: false` blendet aus. Sie werden nach jeder 4. Meldung eingestreut.
- `news_sammeln.py` – nur Standardbibliothek; schreibt `news.json` (Titel, Link, Quelle, Bereich, Datum; kein Fließtext der Verlage)
- `einordnen.py` – KI-Einordnung mit Claude Sonnet 5 (Secret `ANTHROPIC_API_KEY`): relevant?, Gewerke, Rollen, Thema,
  „Was heißt das für dich?“, „Was tust du jetzt?“, Frist, Gewicht. Die KI sieht nur Überschrift und Anrisstext. Ergebnis je
  Link in `einordnung.json` (30 Tage), damit jede Meldung nur einmal eingeordnet wird. Gewerke wie das Pipedrive-Feld „Gewerk“.
  Ohne Secret läuft alles weiter, nur ohne Einordnung.
- `.github/workflows/sammeln.yml` – GitHub Action, committet `news.json` und `einordnung.json` bei Änderungen

Das Widget lädt `https://raw.githubusercontent.com/Institut-Perspektive-Handwerk/academy-news/main/news.json`
(GitHub liefert raw-Dateien mit CORS-Freigabe aus). Fällt eine Quelle aus, läuft der Rest weiter; kommt gar nichts,
wird die Action rot.

Rechtliches: Es werden nur Überschriften mit Link gezeigt, kein Text der Verlage.

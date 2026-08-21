# Security Policy

## Grundprinzipien

1. **Read-only by default.** Ohne `GEKKO_ALLOW_WRITE=true` registriert der Server
   keine Steuer-Tools.
2. **Deny-List ist absolut:** `alarmsystem`, `accessdoors`, `door_intercom`, Kameras —
   niemals steuerbar, unabhängig von jeder Konfiguration.
3. **Kritische Befehle** verlangen eine explizite Bestätigung (`confirm`-Parameter).
4. **Credentials** kommen ausschließlich aus Umgebungsvariablen und werden in Logs
   geschwärzt. Niemals Keys in Configs, Code oder Issues posten.
5. **HTTP-Transport** nur mit Bearer-Token (`GEKKO_HTTP_TOKEN`); ohne Token startet
   er nur mit ausdrücklichem `GEKKO_HTTP_ALLOW_NO_AUTH=true` (nur lokal testen!).
6. **Rate-Limits + Backoff** schützen den Regler (fehlerhafte Requests erscheinen
   im Alarmprotokoll des Kunden-Geräts — bitte nicht fluten).

## Schwachstelle melden

Bitte **kein öffentliches Issue** für sicherheitsrelevante Funde. Schreib an den
Maintainer (GitHub-Profil `joeexafionbot-dev`) oder eröffne ein privates
Security Advisory über GitHub („Security" → „Report a vulnerability").
Wir antworten schnellstmöglich, in der Regel binnen 72 Stunden.

## Gute Praxis für Nutzer

- Für Tests den öffentlichen Mustermann-Demo-Regler verwenden, nie fremde Anlagen.
- `GEKKO_ALLOW_WRITE=true` nur setzen, wenn du weißt, was du tust — und nie auf
  einem Regler, der nicht deiner ist.
- Query-API-Keys regelmäßig rotieren (myGEKKO-Konto → Plus Query API → Verwaltung).

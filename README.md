# KOVA MCP

**Sprich mit deinem myGEKKO.** Ein Open-Source-[MCP](https://modelcontextprotocol.io)-Server,
der Claude (und jeden anderen MCP-Client) mit myGEKKO-Gebäudereglern verbindet — über die
offizielle myGEKKO Query API. *Talk to your myGEKKO building controller from Claude — English
quickstart below.*

> „Wie warm ist es im Wohnzimmer?" · „Welche Lichter sind noch an?" · „Mach die
> Rollläden im Süden runter." — in deinen Worten, direkt an dein Haus.

**KOVA MCP ist das Werkzeug für Bastler und Power-User.** Wer das täglich, am Handy
und für die ganze Familie will — ohne Terminal: das ist die **KOVA App**
([kova.casa](https://kova.casa)).

---

## In 2 Minuten ausprobieren — ohne eigenen Regler

Der öffentliche myGEKKO-Vorführregler („Mustermann") ist für genau solche Tests da:

```bash
claude mcp add mygekko \
  --env GEKKO_USERNAME=mustermann@my-gekko.com \
  --env GEKKO_KEY=HjR9j4BrruA8wZiBeiWXnD \
  --env GEKKO_GEKKOID=K999-7UOZ-8ZYZ-6TH3 \
  -- uvx --from git+https://github.com/joeexafionbot-dev/kova-mcp mygekko-mcp
```

Dann in Claude Code: *„Welche Systeme hat mein Gebäude? Zeig mir die Lichter im Wohnzimmer."*

## Mit deinem eigenen myGEKKO

Voraussetzung: Regler auf [my-gekko.com](https://www.my-gekko.com) registriert und der
Plus-Dienst **„myGEKKO Query API"** aktiviert (im Home Bundle enthalten; Key erzeugen unter
*Einstellungen → Globus → Plus Erweitert → Plus Query API → Verwaltung → Neu generieren*).

```bash
claude mcp add mygekko \
  --env GEKKO_USERNAME=deine@mail.de \
  --env GEKKO_KEY=DEIN-QUERY-API-KEY \
  --env GEKKO_GEKKOID=XXXX-XXXX-XXXX-XXXX \
  -- uvx --from git+https://github.com/joeexafionbot-dev/kova-mcp mygekko-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mygekko": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/joeexafionbot-dev/kova-mcp", "mygekko-mcp"],
      "env": {
        "GEKKO_USERNAME": "deine@mail.de",
        "GEKKO_KEY": "DEIN-QUERY-API-KEY",
        "GEKKO_GEKKOID": "XXXX-XXXX-XXXX-XXXX"
      }
    }
  }
}
```

Auch **lokal** (LAN statt Cloud) möglich: `GEKKO_MODE=local` + `GEKKO_LOCAL_HOST` —
alle Variablen in [`.env.example`](.env.example).

## Was der Server kann

| | |
|---|---|
| **Resources** | `gekko://inventory` (alle Systeme/Geräte, datengetrieben entdeckt), `gekko://system/{system}`, `gekko://item/...`, `gekko://capabilities` |
| **Lese-Tools** | `list_devices`, `get_state`, `describe_capabilities` |
| **Steuer-Tools** | `control_device`, `control_light`, … — **standardmäßig AUS** |
| **Transporte** | `stdio` (Claude Code/Desktop) und `streamable-http` (gehostet, Token-geschützt) |

## Sicherheit (bewusst konservativ)

- **Read-only by default.** Steuern erfordert explizit `GEKKO_ALLOW_WRITE=true`.
- **Deny-List bleibt immer zu:** Alarmanlage, Türen/Zutritt, Kameras sind auch mit
  Write-Freigabe gesperrt; kritische Aktionen verlangen eine zweite Bestätigung.
- **Credentials nur über Umgebungsvariablen**, nie in Dateien/Logs (Key wird in
  Logs geschwärzt).
- **Regler-schonend:** Rate-Limits und Backoff bei `429/470` — fehlerhafte Anfragen
  landen sonst im Alarmprotokoll des Reglers.

Details: [`SECURITY.md`](SECURITY.md).

## English quickstart

MCP server for myGEKKO building controllers (official Query API, cloud or LAN).
Read-only by default; writes are opt-in (`GEKKO_ALLOW_WRITE=true`) and policy-gated
(alarm/access/cameras always denied). Try it instantly against the public demo
controller using the credentials in the first snippet above, or set
`GEKKO_USERNAME` / `GEKKO_KEY` / `GEKKO_GEKKOID` for your own controller. Run via
`uvx --from git+https://github.com/joeexafionbot-dev/kova-mcp mygekko-mcp`.

## Entwicklung

```bash
uv sync && uv run pytest          # Tests
uv run mygekko-mcp --transport stdio
uv run dump-inventory             # read-only Inventar-Dump als JSON
```

Dieses Repo ist der **Release-Mirror** des KOVA-Monorepos (dort läuft die CI);
Issues und PRs sind hier trotzdem willkommen und werden zurückgespielt.

## Lizenz & Marken

[MIT](LICENSE). myGEKKO ist eine Marke der myGEKKO | Ekon GmbH — KOVA MCP ist ein
unabhängiges Community-Projekt und wird nicht von myGEKKO herausgegeben. Die
Demo-Zugangsdaten oben gehören zum öffentlichen Vorführregler von myGEKKO.

# plinth

Le socle commun des IHM web locales : un démon HTTP gardé sur `127.0.0.1`, une feuille de style,
des utilitaires de page, un lanceur d'onglet de terminal et des outils de test. Extrait de
[docpin](https://github.com/elphono/docpin) et généralisé.

**Une bibliothèque, pas un cadre.** Chaque IHM garde son démon, ses routes et sa page ; elle
assemble les pièces dont elle a besoin (≈ 60 lignes de câblage, voir `examples/hello`).
**Aucune dépendance d'exécution** : bibliothèque standard seulement (`dependencies = []`),
Python ≥ 3.12.

```
            ┌───────────── plinth (ce paquet) ─────────────┐
            │ server.py   gardes, routeur, statiques, stale │
            │ static/     ihm.css, ihm.js (Ihm.*)           │
            │ client.py   config.py   platform.py           │
            │ browser.py  terminal.py testing/              │
            └───────────────────────┬──────────────────────┘
                     importe        │        importe
          ┌─────────────────────────┴───────────────────────┐
   ┌──────┴──────┐                                   ┌───────┴──────┐
   │  une IHM A  │  son démon, ses routes, sa page,   │  une IHM B   │
   │             │  sa palette, ses programmes        │              │
   └─────────────┘  interdits en test                 └──────────────┘
```

## Ce que le socle fournit, ce que l'IHM fournit

| Pièce | Le socle fournit | L'IHM fournit |
|---|---|---|
| `plinth.server` | `App(name, header, port, *, code_dirs)`, `@app.route(method, pattern)`, `Refused`, les gardes appliquées **avant** toute route, `stale`, `serve(app)` | ses routes, ses exceptions → codes HTTP (`map_error`), ses fils de fond |
| `static/ihm.css` | jetons sémantiques clair / sombre / forcé, composants | une feuille chargée après, qui redéfinit des jetons et ajoute ses blocs |
| `static/ihm.js` | l'espace de noms `Ihm` (script classique) | sa fonction `render(data)` et ses gestionnaires |
| `plinth.client`, `config`, `platform`, `browser` | appel au démon, démarrage à la demande, configuration env > TOML > défaut, ouvrir une URL | son nom, son dataclass de réglages |
| `plinth.terminal` | `open_terminal(script_lines, title, …)` : un onglet sur un script écrit sur disque | le contenu du script (une commande quelconque) |
| `plinth.testing` | gardes pytest autouse + leur témoin, `run_browser_check`, `helpers.mjs` | sa liste de programmes interdits, ses scripts `.mjs` |

**À venir, hors de cette version** : `service.py` (unité systemd, Restart / Stop),
`runtime.py` (copie stable hors du cache d'un plugin), la pastille Windows, le moteur de
mutation générique.

## Exemple minimal

```python
from pathlib import Path
from plinth import App, Refused, Request, serve
from plinth.server import file_response

HERE = Path(__file__).resolve().parent
app = App("hello", header="X-Hello", port=8770, code_dirs=[HERE])
app.add_static("/static/hello", HERE / "static")      # ses propres statiques
app.map_error(KeyError, 404)

@app.route("GET", "/")
def index(req: Request):
    return file_response(HERE / "static" / "index.html")

@app.route("POST", "/api/items/{item}/tone")
def tone(req: Request):
    body = req.json_object()                            # 400 si ce n'est pas un objet JSON
    if body.get("tone") not in ("ok", "warn"):
        raise Refused(400, "tone inconnu")
    return {"message": f"{req.params['item']} est maintenant {body['tone']}"}

serve(app)          # 127.0.0.1:8770, jusqu'à SIGTERM / SIGINT
```

```html
<link rel="stylesheet" href="/plinth/static/ihm.css">
<link rel="stylesheet" href="/static/hello/hello.css">
…
<span class="daemon" data-ihm-daemon></span>
<script src="/plinth/static/ihm.js"></script>
<script>
  Ihm.configure({ header: 'X-Hello', name: 'hello' });
  Ihm.poll({ url: '/api/items', render: (data) => { /* dessiner */ } });
</script>
```

L'exemple complet, qui sert aux tests navigateur : `uv run python examples/hello/app.py`.

## L'API Python

### `plinth.server`

| Signature | Rôle |
|---|---|
| `App(name, header, port, *, code_dirs, max_body=2 Mio)` | `header` **obligatoire, sans défaut** (une IHM qui l'oublie ne démarre pas), commence par `X-` ; `code_dirs` (mot-clé obligatoire) : les dossiers dont le démon exécute les modules, pour `stale` ; `port` 0 = un port libre |
| `@app.route(method, pattern)` | `GET POST PUT PATCH DELETE` (HEAD suit GET) ; `{nom}` capture un segment décodé, `{nom:path}` la suite (en dernier) ; la première route qui correspond gagne ; `/plinth/…` est réservé |
| le gestionnaire `f(req: Request)` | rend un `Response`, `None` (204) ou une valeur JSON (200) |
| `Request` | `.method .path .segments .params .query .headers .body`, `.json()`, `.json_object()`, `.arg(nom)` |
| `Refused(status, message)` | réponse `{"error": message}` avec ce code, connexion fermée |
| `app.map_error(TypeException, status)` | une exception de l'IHM → un code ; le reste → 500 journalisé |
| `app.add_static(prefix, dossier)` | sert les fichiers **listés** sous `dossier` (un `Path` ou un `importlib.resources` Traversable) |
| `app.background(nom, cible)` | un fil lancé par `serve`, `cible(evenement_d_arret)` |
| `app.stale` | vrai dès que le code sur disque (plinth **et** `code_dirs`) n'est plus celui qui tourne |
| `app.make_server(port=None)` | le serveur lié à `127.0.0.1` (pour les tests : `port=0`) ; `.port`, `.base_url()` |
| `serve(app, *, port=None, on_ready=None) -> int` | sert jusqu'à SIGTERM / SIGINT (fil principal) ; `on_ready(url)` |
| `json_response`, `html_response`, `file_response`, `redirect`, `Response(status, body, content_type, headers)` | les réponses non JSON |

Routes réservées : `GET /plinth/status` → `{name, stale, plinth, port}` (ce que la page interroge),
`GET /plinth/static/<nom>` → `ihm.css`, `ihm.js`.

**Les gardes, appliquées par `App` avant toute route, dans cet ordre** :

| Garde | Refuse | Code |
|---|---|---|
| `Host` = `localhost`, `127.0.0.1` ou `[::1]` au bon port | le rebinding DNS | 403 |
| l'en-tête maison (`header`) sur toute écriture | une page web étrangère : elle ne peut pas l'ajouter sans pré-vol CORS, jamais accordé | 403 |
| `Origin`, s'il est présent, local | une écriture depuis une autre origine | 403 |
| corps borné (`max_body`), jamais `chunked` | | 413 / 411 |

Un refus ferme la connexion (le corps non lu serait pris pour la requête suivante). Toute
réponse porte `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, et refuse d'être
encadrée (`X-Frame-Options: DENY`, `frame-ancestors 'none'`) : une page étrangère ne peut pas
glisser l'IHM dans un cadre invisible pour emprunter les clics. Le socket est lié à `127.0.0.1`
et rien ne le change ; l'URL donnée est en `127.0.0.1`, jamais `localhost` (sous WSL en réseau
miroir, Windows résout `localhost` en `::1` d'abord, que le socket ne reçoit jamais).

### `plinth.client`

`call(port, method, path, *, header, body=None, timeout=10) -> dict` ;
`ensure_daemon(port, start, *, header, probe="/plinth/status", wait=8.0) -> str` ;
`ClientError(.status)`, `DaemonUnreachable`.

### `plinth.config`

```python
@dataclass(frozen=True)
class Settings:
    port: int = field(default=8770, metadata={"min": 1, "max": 65535})
    terminal: tuple[str, ...] = ()

settings = config.load("hello", Settings)     # HELLO_PORT > config.toml > défaut
```

`load(name, schema)`, `config_path(name)` (`~/.config/<nom>/config.toml` ou `<NOM>_CONFIG`),
`data_dir(name)` (`~/.local/share/<nom>` ou `<NOM>_DATA_DIR`), `env_prefix(name)`,
`ConfigError`. Types : `str int float bool Path tuple[str, ...]`. Une clé inconnue du fichier
TOML est une erreur, pas un silence.

### `plinth.platform`, `plinth.browser`

`platform() -> "wsl" | "linux" | "macos" | "other"` (forçable par `PLINTH_PLATFORM`),
`wsl_distro()`. `open_url(url) -> str` (l'ouvreur utilisé : `wslview`, `cmd.exe`,
`explorer.exe`, `xdg-open`, `open`), `OpenError`.

### `plinth.terminal`

```python
open_terminal(script_lines, title, *, data_dir, keep_open, cwd=None, template=()) -> str
quote(argv) -> str          # une ligne shell qui lance argv tel quel : ne jamais en composer une à la main
```

Le script est écrit sous `data_dir/launch/`, s'efface en démarrant, et s'ouvre dans un onglet :
`wt.exe -w 0 new-tab --title <titre> -- wsl.exe -d <distro> -e bash -l <script>` sous WSL,
un terminal natif ailleurs, ou le `template` fourni (qui contient `{script}`). **Jamais de `;`
dans la ligne de commande** (`wt.exe` le prend pour un séparateur) : `command()` refuse tout
argument qui en porte un. `keep_open` (obligatoire) laisse un shell dans l'onglet une fois le
script fini, avec son code de sortie.

### `plinth.testing`

```python
# conftest.py de l'IHM : UNE fixture par nom (un tuple de fixtures sous un seul
# nom n'enregistrerait rien, en silence ; le témoin ci-dessous l'attrape)
from plinth.testing import isolate, make_guards
_guards = make_guards(forbidden={"make", "ssh"})     # + PLATFORM_PROGRAMS, toujours
_isolated = isolate("hello")                          # HELLO_CONFIG, HELLO_DATA_DIR dans tmp_path

# un test de l'IHM : le témoin que les gardes sont armées
from plinth.testing import PLATFORM_PROGRAMS, assert_guards_armed, run_browser_check

def test_guards_are_armed(tmp_path):
    assert_guards_armed(tmp_path, {"make", "ssh"} | PLATFORM_PROGRAMS)

# un test navigateur
@pytest.mark.browser
def test_page(tmp_path):
    run_browser_check(build_app(), Path(__file__).parent / "browser" / "page_check.mjs",
                      out_dir=tmp_path)
```

La garde refuse un programme interdit lancé directement, à travers un enrobeur (`env`,
`bash -c`, `timeout`…) ou dans une ligne de shell, par `subprocess`, `os.system` ou
`os.posix_spawn` ; elle refuse tout `os.killpg` et tout `os.kill` d'un processus que le test
n'a pas lancé.

`run_browser_check` démarre le démon de test sur le port 0 dans un fil et lance
`node <script> <dossier-playwright> <url> <dossier-captures> <args…>`. Sans
`PLINTH_PLAYWRIGHT_DIR` (un dossier dont `node_modules` contient playwright), il **échoue**, il
ne saute pas. `PLINTH_BROWSER_OUT` garde les captures. Le script importe les aides :

```js
const { start } = await import(process.env.PLINTH_HELPERS);
const t = await start();          // t.page, t.base, t.fail, t.step, t.shots(nom), t.finish()
```

`finish()` échoue si la page a levé une erreur, écrit une erreur en console ou fait une requête
hors du démon.

## L'API de la page : `Ihm.*`

| Appel | Rôle |
|---|---|
| `Ihm.configure({header, name})` | **d'abord** : l'en-tête de garde du démon, et le nom (préfixe du `localStorage`) |
| `Ihm.call(method, path, body)` | `fetch` + en-tête + JSON ; lève `Error(message)` avec `.status` |
| `Ihm.act(bouton, run)` | un clic à la fois : `run()` rend la phrase du toast, puis la page se rafraîchit |
| `Ihm.say(texte, erreur)` | le toast |
| `Ihm.poll({url, render, every, onRestart})` | boucle de rafraîchissement : `render(data)` seulement si les données changent, **jamais** pendant un menu ouvert, un dialogue ou un clic en cours (rattrapé à la fermeture) ; bandeaux « démon injoignable » et « démon périmé » ; `[data-ihm-daemon]` tenu à jour |
| `Ihm.confirm({title, lines, items, limit, kept, label, tone, run})` | le dialogue de confirmation : compte, liste, et affiche l'erreur **dans** le dialogue, qui reste ouvert ; `tone: 'irreversible'` met le focus sur Annuler |
| `Ihm.picker({title, lines, load, row, match, choose})` | une liste filtrable au clavier (↑ ↓ Entrée Échap) dans un dialogue |
| `Ihm.foldable(section, tête, clé, quoi)` | une tête qui replie sa section, mémorisée sous `<nom>.folded.<clé>` ; marche sans `localStorage` |
| `Ihm.button`, `Ihm.menu`, `Ihm.icon`, `Ihm.addIcon`, `Ihm.chip`, `Ihm.badge`, `Ihm.empty`, `Ihm.make`, `Ihm.plural`, `Ihm.ago` | petits constructeurs ; les données n'atteignent le DOM que par `textContent` |
| `Ihm.blocked()`, `Ihm.closeMenus()` | l'état de la garde de redessin |

### Les jetons et les composants de `ihm.css`

| Jeton | Sens | Classe de ton |
|---|---|---|
| `--ok` | fait, sain, passé | `.is-ok` |
| `--warn` | attention : regarder avant de continuer | `.is-warn` |
| `--error` | échoué, refusé, manquant | `.is-error` |
| `--live` | en cours, maintenant | `.is-live` |
| `--human` | attend un geste humain | `.is-human` |
| `--irreversible` | ne se défait pas une fois lancé | `.is-irreversible` |
| `--info` | information neutre | `.is-info` |

Chaque sens a trois nuances (`--x`, `--x-text`, `--x-soft`). Thème : clair par défaut, sombre
sous `prefers-color-scheme: dark`, forcé par `<html data-theme="light|dark">`.

Composants : `.topbar`, `.daemon`, `.card`, `.tiles`/`.tile`, `.badge`, `.chip`, `button.primary`
/ `.ghost` / `.danger`, `dialog.ihm-dialog`, `.ihm-toast`, `details.ihm-menu`, `.fold-toggle`,
`.section` à rail (`.section-head`, `.section-body`), `.group`, `.row` à liseré coloré,
`.empty`, `.segmented`, `.ihm-banner`.

## Installer plinth dans une IHM

Depuis GitHub, à une étiquette (HTTPS, aucun identifiant) :

```bash
uv add "plinth @ git+https://github.com/elphono/plinth@v0.1.0"
```

ou par chemin, en développement (modifiable), depuis un clone voisin :

```bash
git clone https://github.com/elphono/plinth ../plinth
uv add --editable ../plinth
```

L'IHM sert alors **ses** statiques à côté de ceux du socle : `/plinth/static/ihm.css` et
`/plinth/static/ihm.js` viennent du paquet installé (`importlib.resources`, aucun chemin
composé), et `app.add_static("/static/<ihm>", dossier)` sert les siens.

## Développer plinth

```bash
uv sync
uv run pytest -q                                      # suite par défaut (construit aussi la roue)
PLINTH_PLAYWRIGHT_DIR=<dossier dont node_modules contient playwright> \
  uv run pytest -m browser -q                         # l'exemple hello dans un vrai Chromium
uv run ruff check . && uv run mypy
```

## Licence

MIT.

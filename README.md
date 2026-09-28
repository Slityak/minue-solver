# minue-solver (`%%solve`)

Colab-ből használható `%%solve` cell magic, ami egy Cloudflare Workeren keresztül a Claude API-val oldja meg a kurzus numpy-feladatait. Claude a kódot ténylegesen lefuttatja (Anthropic code execution tool), nem fejben számol, a rendszerprompt pedig a kurzus notebookjainak konvencióit rögzíti.

```
Colab %%solve ──HTTPS + Bearer token──▶ Cloudflare Worker ──▶ Claude API (code execution)
      ▲                                        │
      └──────── JSON: answer, code, explanation, stdout ◀──┘
```

## Felépítés

| Mappa | Tartalom |
|---|---|
| `worker/` | TypeScript Worker: `GET /health`, `POST /solve`, token-ellenőrzés, Claude-hívás, válasz-parszolás |
| `src/minue_solver/`, `tests/`, `pyproject.toml` | `minue-solver` csomag (a repó gyökere): `SolverClient` és a `%%solve` IPython extension |
| `demo.ipynb` | Védésre előkészített Colab notebook |

## 1. Worker telepítése

```bash
cd worker
npm install
npx wrangler login
npx wrangler secret put ANTHROPIC_API_KEY   # console.anthropic.com
npx wrangler secret put SOLVER_TOKEN        # ugyanaz, mint a src/minue_solver/client.py DEFAULT_SETTINGS-ében
npm run deploy
```

Ellenőrzés: `https://minue-solver.<fiók>.workers.dev/health` → `{"status":"ok"}`.
A modellt a `wrangler.toml` `MODEL` változója állítja (alapértelmezés: `claude-opus-5-5`).

## 2. Colab beállítása

Nincs mit beállítani: a Worker URL-je és a token be van építve a csomagba (`DEFAULT_SETTINGS` a `client.py`-ban). Ha másik Workert használnál, a Colab Secrets-ben vagy környezeti változóban megadott `SOLVER_URL` / `SOLVER_TOKEN` felülírja ezeket.

Setup-cella (minden runtime-indítás után egyszer; frissítés után előbb Runtime → Restart session, mert a Python a már betöltött régi modult használja):

```python
!pip install -q --force-reinstall --no-deps "git+https://github.com/Slityak/minue-solver.git"
%load_ext minue_solver
```

## 3. Használat

```python
%%solve
# Adott a következő mátrix X=np.array([[-7,-7,7,-1],[5,-5,5,7],[9,-4,7,-10]]) ...
# Mi lesz a 2. és 0. megfigyelés közötti Manhattan távolság?
```

A `%%solve` sor helyére a cellába íródik egy rövid, komment nélküli Python-kód a kommentjeid alá (köztük egy üres sorral), és le is fut a notebook névterében. A kimenet pontosan az, amit a kód kiír, semmi más: mintha a cellát te futtattad volna. Ha a futtatás elhal (pl. eltérő numpy-verzió miatt), a hibát csendben visszaküldi javításra, és csak a sikeres futás kimenete jelenik meg; ha minden próbálkozás elhal, az utolsó traceback látszik.

| Kapcsoló | Hatás |
|---|---|
| *(nincs)* | kód a cellába, kimenet = a kód kimenete, hiba esetén automatikus javítás |
| `--explain` | ugyanez, a kimenet előtt lépésenkénti magyarázattal magyarul |
| `--check 27` | ellenőrzi a saját válaszod; ha hibás, tippet ad a helyes szám elárulása nélkül |
| `--no-run` | csak beírja a kódot a cellába, nem futtatja |
| `--keep` | nem írja át a cellát, a kód a kimenetben jelenik meg |
| `--fixes N` | legfeljebb hányszor kérjen javítást (alapértelmezés: 2) |

Ha a kódot saját cellába másolod, és ott hal el, írd a következő cellába:

```python
%fix
```

Ez az előző cella kódját és a hibaüzenetét küldi el javításra (ha az előző cella egy `%%solve` volt, akkor annak utolsó kódját), és a javított kódot a `%fix` cellájába írja. A `%fix` után megadhatod a feladatot is, ha kontextus kell hozzá.

## Tesztek

```bash
cd worker && npm test && npm run typecheck      # 14 teszt, mockolt Claude API
pip install -e ".[dev]" && pytest  # 23 teszt, valódi IPython shellben
```

## Biztonsági megjegyzések

- Az API-kulcs csak Worker secretként létezik, a kliens sosem látja.
- A `/solve` Bearer tokent kér, időállandó összehasonlítással. A token a publikus csomagban van, tehát nem titok: a valódi védelem a Worker rate limitje (IP-nként 10, összesen 30 kérés percenként) és az Anthropic Console-ban beállított havi költési limit. Visszaéléskor új tokent kell generálni, és a Workerben meg a `client.py`-ban is cserélni.
- A helyi futtatás `exec`-et használ: ez azért elfogadható, mert a kód a saját Workerünkből jön. Idegen végpontra állítva használd a `--no-run` kapcsolót.
- A `fix` módban a kód és a hibaüzenet együtt legfeljebb 2×10 000 karakter lehet.
- A kérdés hossza 5000 karakterre korlátozott, hogy a kulcsot ne lehessen nagy kérésekkel égetni.

## Demó ellenőrzőlista

1. Runtime → Restart session
2. Setup-cella, majd a `/health` cella
3. Regressziós cellák: whitening főátló-összeg = **0.5663**, Manhattan távolság = **28**
4. Tartalék: előre lefuttatott, mentett notebook és képernyőfelvétel, ha nincs hálózat

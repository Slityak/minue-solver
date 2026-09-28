# Data-mining solver (`%%solve`)

Colab-ből használható `%%solve` cell magic, ami egy Cloudflare Workeren keresztül a Claude API-val oldja meg az adatbányászat kurzus numpy-feladatait. Claude a kódot ténylegesen lefuttatja (Anthropic code execution tool), nem fejben számol, a rendszerprompt pedig a kurzus notebookjainak konvencióit rögzíti.

```
Colab %%solve ──HTTPS + Bearer token──▶ Cloudflare Worker ──▶ Claude API (code execution)
      ▲                                        │
      └──────── JSON: answer, code, explanation, stdout ◀──┘
```

## Felépítés

| Mappa | Tartalom |
|---|---|
| `worker/` | TypeScript Worker: `GET /health`, `POST /solve`, token-ellenőrzés, Claude-hívás, válasz-parszolás |
| `python/` | `datamining-solver` csomag: `SolverClient` és a `%%solve` IPython extension |
| `demo.ipynb` | Védésre előkészített Colab notebook |

## 1. Worker telepítése

```bash
cd worker
npm install
npx wrangler login
npx wrangler secret put ANTHROPIC_API_KEY   # console.anthropic.com
npx wrangler secret put SOLVER_TOKEN        # tetszőleges hosszú véletlen szöveg
npm run deploy
```

Ellenőrzés: `https://datamining-solver.<fiók>.workers.dev/health` → `{"status":"ok"}`.
A modellt a `wrangler.toml` `MODEL` változója állítja (alapértelmezés: `claude-opus-5-5`).

## 2. Colab beállítása

A kulcs ikonnál (Secrets) vedd fel, és engedélyezd a notebook hozzáférést:

- `SOLVER_URL` = a Worker URL-je
- `SOLVER_TOKEN` = ugyanaz, mint a Workernél

Setup-cella (minden runtime-indítás után egyszer):

```python
!pip install -q "git+https://github.com/Slityak/minue-solver.git#subdirectory=python"
%load_ext solver
```

## 3. Használat

```python
%%solve
# Adott a következő mátrix X=np.array([[-7,-7,7,-1],[5,-5,5,7],[9,-4,7,-10]]) ...
# Mi lesz a 2. és 0. megfigyelés közötti Manhattan távolság?
```

A válasz egy rövid, komment nélküli Python-kód, ami a cellába íródik a kommentjeid alá, a `%%solve` sor helyére (a kimenetben az eredmény és a helyi futtatás kimenete látszik, a kód lenyitható tartalékként). Újrafuttatva a cella már sima Python-kódként fut. A kódot a Colab helyben is lefuttatja. Ha ott elhal (pl. eltérő numpy-verzió miatt), a hibát automatikusan visszaküldi javításra, és a javított kódot újra lefuttatja.

| Kapcsoló | Hatás |
|---|---|
| *(nincs)* | eredmény + kód + helyi futtatás, hiba esetén automatikus javítás |
| `--explain` | ugyanez, lépésenkénti magyarázattal magyarul |
| `--check 27` | ellenőrzi a saját válaszod; ha hibás, tippet ad a helyes szám elárulása nélkül |
| `--no-run` | csak a kódot kéri le, helyben nem futtatja |
| `--keep` | nem írja át a cellát, a kód csak a kimenetben jelenik meg |
| `--fixes N` | legfeljebb hányszor kérjen javítást (alapértelmezés: 2) |

Ha a kódot saját cellába másolod, és ott hal el, írd a következő cellába:

```python
%fix
```

Ez az előző cella kódját és a hibaüzenetét küldi el javításra (ha az előző cella egy `%%solve` volt, akkor annak utolsó kódját), és a javított kódot a `%fix` cellájába írja. A `%fix` után megadhatod a feladatot is, ha kontextus kell hozzá.

## Tesztek

```bash
cd worker && npm test && npm run typecheck      # 13 teszt, mockolt Claude API
cd python && pip install -e ".[dev]" && pytest  # 21 teszt, valódi IPython shellben
```

## Biztonsági megjegyzések

- Az API-kulcs csak Worker secretként létezik, a kliens sosem látja.
- A `/solve` Bearer tokent kér, időállandó összehasonlítással.
- A helyi futtatás `exec`-et használ: ez azért elfogadható, mert a kód a saját Workerünkből jön. Idegen végpontra állítva használd a `--no-run` kapcsolót.
- A `fix` módban a kód és a hibaüzenet együtt legfeljebb 2×10 000 karakter lehet.
- A kérdés hossza 5000 karakterre korlátozott, hogy a kulcsot ne lehessen nagy kérésekkel égetni.

## Demó ellenőrzőlista

1. Runtime → Restart session
2. Setup-cella, majd a `/health` cella
3. Regressziós cellák: whitening főátló-összeg = **0.5663**, Manhattan távolság = **28**
4. Tartalék: előre lefuttatott, mentett notebook és képernyőfelvétel, ha nincs hálózat

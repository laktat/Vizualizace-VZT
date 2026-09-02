# Vizualizace a monitoring technologií závodu

Simulace a monitoring technologického zázemí smyšleného výrobního závodu.
Všechna zařízení komunikují přes **Modbus TCP** — stejně jako regulátory
v reálném rozvaděči. Nad nimi běží sběr dat do SQLite a vyhodnocení provozu.

Projekt vznikl jako ukázka toho, jak se dělá dispečink budov: mapa registrů
oddělená od kódu, sběr dat odolný proti výpadku, vyhodnocení postavené na
fyzice zařízení a vizualizace, ze které se dá číst stav provozu.

## Co závod obsahuje

| Zařízení | Popis | Modbus |
|---|---|---|
| **VZT 1** | Výrobní hala A — 45 000 m³/h, rekuperace, vodní ohřívač i chladič | `127.0.0.1:5021` |
| **VZT 2** | Lakovna — 16 000 m³/h, nízká rekuperace, rychle se zanášející filtry | `127.0.0.1:5022` |
| **VZT 3** | Sklad a administrativa — 12 000 m³/h | `127.0.0.1:5023` |
| **Chiller 1–3** | Chladicí jednotky, každá 2 kompresory, tlaky chladiva, motohodiny | `:5031–5033` |
| **Chladicí věž** | 2 ventilátory, mokrý teploměr, approach, dopouštění a odluh | `:5034` |
| **Okruh chlazené vody** | 2 primární + 2 sekundární čerpadla (provoz/záloha), průtoky, tlaky | `:5035` |
| **Kotel 1–2** | Plynové kondenzační kotle 400 kW, modulovaný hořák | `:5051–5052` |
| **Kotelna** | Rozdělovač/sběrač, ekvitermní regulace, 2 oběhová čerpadla | `:5053` |

Zařízení nejsou nezávislé ostrovy — jsou spojená tak, jak v závodě teče teplo:

```
počasí ─► 3× VZT jednotka (každá svoje hala, svoje regulace)
             │ odebraný chlad          │ odebrané teplo
             ▼                          ▼
      okruh chlazené vody         okruh topné vody
             │ zpátečka                 │ zpátečka
             ▼                          ▼
        3× chiller                  2× kotel
             │ odpadní teplo
             ▼
      chladicí věž ─► venkovní vzduch
```

Když v lakovně otevře chladicí ventil, za chvíli se ohřeje zpátečka chlazené
vody, naskočí další kompresor a rozběhne se druhý ventilátor na věži.

## Spuštění

```bash
pip install -r requirements.txt

python simulator.py          # závod na Modbus TCP (11 zařízení)
python -m web.server         # dispečink na http://127.0.0.1:8000
python poller.py             # (volitelně) archivace dat do data.sqlite
```

Dispečink si čte zařízení sám přes Modbus, poller tedy není potřeba —
běží vedle jako archiv pro dlouhodobé trendy a vyhodnocení v `analysis.py`.

Simulace běží ve zrychleném čase — výchozí `--speed 60` znamená, že jedna
reálná sekunda je minuta provozu, takže denní cyklus proběhne za 24 minut
a motohodiny narůstají viditelně.

```bash
python simulator.py --season leto      # horký den, chillery a věž na doraz
python simulator.py --season zima      # kotelna naplno, chlazení odstavené
python simulator.py --speed 300        # rychlejší běh času
python plant.py                        # vypíše soupis zařízení a portů
python poller.py --device vzt1 --once  # jeden odečet jednoho zařízení
```

### Poruchy pro test vyhodnocení

```bash
python simulator.py --fault vzt1:stuck-valve --fault chw:p1 --fault vez:fan1
```

| Zařízení | Porucha | Co se stane |
|---|---|---|
| VZT | `stuck-valve` | pohon topného ventilu se zasekne — topí i při povelu 0 % |
| VZT | `sensor-fail` | čidlo přívodu hlásí −120 °C, regulace přejde na náhradní čidlo |
| VZT | `fan-fault` | výpadek přívodního ventilátoru |
| Chiller | `comp1`, `comp2` | porucha kompresoru, jednotka jede na půl výkonu |
| Věž | `fan1`, `fan2` | výpadek ventilátoru, roste approach |
| Okruh chladu | `p1`, `p2`, `s1`, `s2` | porucha čerpadla — záloha naskočí bez přerušení průtoku |
| Kotelna | `hp1`, `hp2` | porucha oběhového čerpadla |
| Kotel | `burner-fault` | hořák nenaběhne, kaskáda přehodí na druhý kotel |

## Vizualizace

Dispečink je jednostránková aplikace: schéma se poskládá jednou a pak už do něj
přes WebSocket každou sekundu přitékají jen hodnoty. Nic se nepřekresluje,
takže ventilátory se točí plynule a v potrubí je vidět, kudy zrovna teče médium.

```
 ┌──────────┐  Modbus TCP   ┌────────────┐  WebSocket  ┌───────────┐
 │ zařízení │ ◄───────────► │   server   │ ◄─────────► │ prohlížeč │
 └──────────┘   čtení 1 s   └────────────┘   stav 1 s  └───────────┘
```

**Přehled závodu** — technologické schéma se třemi VZT jednotkami, rozvody topné
a chlazené vody, kotelnou a strojovnou chlazení. Z každého celku se prokliká do
detailu.

**Detail VZT jednotky** — cesta vzduchu od sání po odpad s čidly na svých místech,
rekuperátor, ohřívač a chladič s polohou ventilů, filtry měnící barvu podle
zanesení a posuvníky žádaných hodnot, které se zapisují zpět do jednotky.

**Výroba chladu** — chladivový okruh každého chilleru (kondenzátor, kompresory,
expanzní ventil, výparník) s tlaky, přehřátím, motohodinami a počty startů,
chladicí věž s approachem a vodním hospodářstvím, hydraulika okruhu s anuloidem
a dvojicemi čerpadel.

**Kotelna** — oba kotle s hořákem, spalinami a modulací, rozdělovač a sběrač,
oběhová čerpadla a ekvitermní regulace.

Ovládání je obousměrné: posuvník zapíše žádanou hodnotu přes Modbus do zařízení
a ve schématu je hned vidět, jak na ni technologie zareagovala.

### Rozhraní serveru

| Cesta | Co dělá |
|---|---|
| `GET /` | vizualizace |
| `GET /api/meta` | soupis zařízení, veličin, jednotek, stavů a alarmů |
| `GET /api/state` | aktuální stav všech zařízení |
| `GET /api/history/{id}?keys=…` | posledních 15 minut pro trendy |
| `POST /api/write` | zápis žádané hodnoty `{device, key, value}` |
| `WS /ws` | stav celého závodu každou sekundu |

## Struktura

```
plant.py        soupis zařízení závodu — co kde stojí a na jakém portu
registers.py    mapy Modbus registrů všech typů zařízení
simulator.py    Modbus TCP server pro každé zařízení
poller.py       sběr dat ze všech zařízení do SQLite
analysis.py     vyhodnocení provozu (filtr, ventil, čidla)
app.py          starší dashboard nad jednou VZT jednotkou (Streamlit)
schematic.py    nákres VZT jednotky jako SVG pro starší dashboard
web/
  server.py     dispečink: Modbus -> WebSocket, zápis žádaných hodnot
  static/
    index.html  kostra aplikace
    screens.js  technologická schémata všech obrazovek
    app.js      spojení, navigace, plnění schémat hodnotami
    style.css   vzhled velínové obrazovky
sim/
  common.py     regulátor PI, model motoru, dvojice provoz/záloha, sekvencer
  factory.py    dispečer — propojuje technologie a počítá počasí
  ahu.py        vzduchotechnická jednotka
  chiller.py    chladicí jednotka s chladivovým okruhem
  tower.py      chladicí věž
  chw.py        okruh chlazené vody s anuloidem
  boiler.py     plynový kotel s modulovaným hořákem
  hw.py         okruh topné vody, ekvitermní regulace, kaskáda kotlů
```

## Co model počítá věrně

**Vzduchotechnika.** Kaskádní regulace: nadřazená smyčka drží teplotu v hale
a počítá z ní žádanou teplotu přívodu, podřízená smyčka ji drží sekvencí
rekuperace → ohřev → chlazení s pásmem necitlivosti, takže jednotka nikdy
netopí a nechladí zároveň. V létě se rekuperátor obchází (noční předchlazení).
Filtr se zanáší tím rychleji, čím víc vzduchu jednotka tlačí.

**Chlazení.** Vypařovací a kondenzační teplota a z nich tlaky chladiva R410A,
přehřátí držené expanzním ventilem, postupné najíždění kompresorů s minimální
dobou chodu a pauzy. COP klesá s rostoucím rozdílem tlaků — když věž nestíhá,
chiller spotřebuje víc proudu za stejný chlad. Nabíhá vždy stroj s nejmenším
počtem motohodin.

**Chladicí věž.** Fyzikální mez je teplota mokrého teploměru; rozdíl proti ní
(approach) ukazuje, jestli věž stíhá. Odpar vodu zahušťuje, proto model počítá
i hladinu, dopouštění a odluh podle vodivosti.

**Hydraulika.** Anuloid odděluje primární okruh od sekundárního: když primární
čerpadlo žene víc vody, než spotřebiče odeberou, přebytek se přelije do
zpátečky a chillery dostanou chladnější směs — pověstná „nízká delta T“.
Sekundární čerpadlo drží otáčkami tlakovou diferenci, takže se zavírajícími
se ventily ubírá.

**Kotelna.** Ekvitermní křivka počítá žádanou teplotu rozdělovače podle venkovní
teploty a nadřazená regulace ji posílá kotlům jako žádanou hodnotu. Hořák má
minimální modulaci, minimální dobu hoření a pauzu, takže na malé zátěži
nemůže cyklovat. Nad nastavenou venkovní teplotou se kotelna odstaví celá.
Účinnost kondenzačního kotle roste s klesající teplotou zpátečky.

**Čerpadla.** Každá dvojice jede jako provoz/záloha: po nastaveném počtu hodin
se automaticky vystřídají, aby se opotřebovávaly rovnoměrně, a při poruše
vedoucího naskočí záloha okamžitě, bez přerušení průtoku.

## Mapa registrů

Vše, co která adresa znamená, je v `registers.py` — u reálné zakázky se sem
přepíšou adresy z dokumentace výrobce a zbytek kódu zůstane stejný. Měřené
hodnoty jsou input registry (funkce 4), nastavení holding registry (funkce 3,
dají se zapisovat). Motohodiny, počty startů a velké průtoky jsou 32bitové
(dva registry), protože 65 535 hodin je jen sedm a půl roku provozu.

## Kam to míří dál

- vyhodnocení z `analysis.py` (predikce výměny filtru, zaseklý ventil, vadné
  čidlo) přenést do dispečinku ke každému zařízení
- kniha alarmů s historií — kdy alarm vznikl, kdy zmizel, kdo ho odkvitoval
- spotřeby a náklady po měsících: plyn, elektřina, voda do věže

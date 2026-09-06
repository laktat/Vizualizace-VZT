# Vizualizace a monitoring technologií závodu

*[English version](README.en.md)*

![Dispečink závodu](docs/dispecink.gif)

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
python poller.py             # archivace dat do data.sqlite
```

Dispečink si čte zařízení sám přes Modbus, takže bez polleru funguje. Poller
běží vedle jako archiv — dispečink si z něj po restartu načte nedávnou
historii, aby vyhodnocení provozu nemuselo začínat od nuly.

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

Poruchy zadané přes `--fault` jsou trvalé příčiny — kvitování je neodstraní,
porucha po něm naskočí znovu. Zablokovaný hořák po nezdařeném zápalu vzniká
sám a kvitovat se dá.

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

![Přehled závodu](docs/prehled.png)

**Detail VZT jednotky** — cesta vzduchu od sání po odpad s čidly na svých místech,
rekuperátor, ohřívač a chladič s polohou ventilů, filtry měnící barvu podle
zanesení a posuvníky žádaných hodnot, které se zapisují zpět do jednotky.

![Detail VZT jednotky](docs/vzt.png)

**Výroba chladu** — chladivový okruh každého chilleru (kondenzátor, kompresory,
expanzní ventil, výparník) s tlaky, přehřátím, motohodinami a počty startů,
chladicí věž s approachem a vodním hospodářstvím, hydraulika okruhu s anuloidem
a dvojicemi čerpadel.

![Výroba chladu](docs/chlazeni.png)

**Kotelna** — oba kotle s hořákem, spalinami a modulací, rozdělovač a sběrač,
oběhová čerpadla a ekvitermní regulace.

![Kotelna](docs/kotelna.png)

**Energie a náklady** — kde se spotřebovává elektřina a plyn, kolik stojí
vyrobená kilowatthodina tepla a chladu, co ušetří rekuperace.

![Energie a náklady](docs/energie.png)

**Alarmy a kvitování** — co právě hoří, s tlačítky na kvitování i odblokování,
a historie: kdy alarm vznikl, kdy zmizel, jak dlouho trval a kdo ho kvitoval.

![Alarmy a kvitování](docs/alarmy.png)

## Energie a náklady

Většinu peněz v takovém provozu spolyká topení a chlazení, jen se to obvykle
nedá rozpadnout na zařízení. Dispečink proto počítá energetickou bilanci
z počítadel podružného měření, která hlásí zařízení ve svých registrech —
elektroměry ventilátorů a kompresorů, plynoměr kotlů, kalorimetry tepla
a chladu. Počítadla narůstají a nikdy se nenulují, stejně jako na skutečném
měřidle; spotřeba za období se z nich počítá rozdílem dvou odečtů.

Obrazovka **Energie a náklady** ukazuje:

**Kde se spotřebovává** — rozpad elektřiny po zařízeních s okamžitým příkonem,
spotřebou a náklady. Bez rozpadu se nedá nic zlepšit: jedno číslo za celý
závod řekne jen to, že je vysoké.

**Měrné ukazatele** — to, s čím se dá porovnávat měsíc proti měsíci:

| Ukazatel | Co říká |
|---|---|
| Chladicí faktor chillerů | vyrobený chlad na kWh elektřiny kompresorů |
| Chladicí faktor strojovny | totéž včetně věže a čerpadel — tohle se platí |
| Účinnost kotelny | vyrobené teplo z energie ve spáleném plynu |
| **Cena chladu a tepla** | kolik stojí vyrobená kWh — v Kč, ne v procentech |
| Podíl rekuperace | kolik z práce výměníků zastal rekuperátor zdarma |
| Měrný příkon ventilátorů | kW na protlačený m³/s; roste se zanášením filtrů |

**Co utíká** — kolik ušetřila rekuperace a kolik se naopak zmařilo, když
jednotka topila a chladila proti sobě. Zmařené teplo se platí dvakrát:
nejdřív se vyrobí a pak ho musí chladič odebrat, takže se cení součtem ceny
tepla a ceny chladu. Je to přímé pokračování diagnostiky zaseklého ventilu —
tam se pozná, že netěsní, tady kolik to dělá za den.

Ceny energií jsou na jednom místě v `plant.py` (`TARIFFS`) a u reálné zakázky
se sem přepíšou sazby z faktury.

## Alarmy, kvitování a záznamník

Alarmy, které hlásí zařízení ve svém bitovém slově, jsou okamžitý stav.
Provozu to nestačí: porucha, která v noci naskočila a do rána zmizela, ráno
na displeji není — a přesto se stala. Dispečink proto sleduje ZMĚNY. Každý
vznik alarmu založí záznam, zánik mu doplní čas ukončení, a kvitování je
třetí, samostatný údaj.

### Kvitování a odblokování jsou dvě různé věci

| | Co dělá | Sahá na zařízení? |
|---|---|---|
| **Kvitovat** | zapíše, kdo a kdy poruchu vzal na vědomí | ne |
| **Odblokovat poruchu** | zapamatovanou poruchu zapomene a stroj smí naběhnout | ano, zápis do registru |

Rozdíl je podstatný. Kvitování je pro dohledatelnost — alarm zůstane, dokud
trvá jeho příčina. Odblokování je zásah do zařízení a jde přes Modbus.

### Poruchy se zapamatují

Skutečný stroj se po poruše sám nerozjede, ani když příčina zmizí — motorová
ochrana zůstane vyhozená, dokud ji někdo nekvituje. Model to dělá stejně:

```
fault    vnější příčina (přetížení, výpadek fáze, zaseklý rotor)
latched  zapamatovaná porucha, která čeká na kvitování
```

Kvitování poruchu zapomene. Když příčina pořád trvá, naskočí okamžitě
znovu — kvitování bez odstranění závady nepomůže ani v poli.

Nejnázornější je to na kotli: když zápal nevyjde (u zdravého hořáku
výjimečně, model s tím počítá), hořák se zablokuje a sám se už nerozjede.
Kaskáda mezitím převezme zátěž druhým kotlem, takže rozdělovač teplotu drží,
ale dokud někdo nepřijde kvitovat, jede kotelna na půl výkonu. Přesně proto
se do kotelen chodí kvitovat poruchy.

### Filtrace zákmitů

Alarm se zapíše, teprve když vydrží nastavenou dobu, a ukončí se, až je
nastavenou dobu pryč. Bez toho by se záznamník zaplnil vteřinovými zákmity
na hranici regulace: "nedosažena žádaná teplota" se u kotle na kraji pásma
objeví a zmizí desetkrát za minutu. Ve zkoušce to udělalo rozdíl mezi
35 záznamy za dvě minuty a třemi — přičemž ty tři byly skutečné poruchy.

Jak dlouho který alarm musí vydržet, je u jeho popisu v `registers.py`.
Porucha stroje nebo čidla se hlásí hned, odchylka od žádané hodnoty až po
minutě.

Záznamník leží ve vlastní databázi `alarms.sqlite`, oddělené od archivu
měření, který plní poller — jeden soubor, jeden zapisovatel.

## Vyhodnocení provozu

U VZT jednotek nestojí vyhodnocení na prahové hodnotě jedné veličiny — to už
umí sám regulátor přes alarmy. Kontroly sledují PRŮBĚH několika veličin za
sebou, a proto poznají věci, které z jedné hodnoty vidět nejsou:

| Kontrola | Na čem stojí |
|---|---|
| **Filtr přívodu a odtahu** | trend tlakové ztráty → kdy narazí na mez výměny |
| **Topný ventil** | topný výkon proti tomu, kolik poloha ventilu dovolí |
| **Rekuperace** | účinnost spočítaná z měřených teplot proti projektové |
| **Vzduchová cesta** | průtok proti tomu, co odpovídá otáčkám ventilátoru |
| **Čidla** | hodnoty mimo fyzikální rozsah (přerušený obvod, zkrat) |
| **Teplota v hale** | odchylka od žádané a jestli jednotce nedošel výkon |

Zjištění mají tři úrovně (k řešení / ke sledování / v pořádku) a promítají se
i do kontrolky u zařízení v levém sloupci, takže si člověk nemusí obrazovky
obcházet. Když na kontrolu nejsou data, řekne to — netvrdí, že je vše v pořádku.

### Časová osa: provozní hodiny, ne kalendář

Predikce zanesení filtru se počítá proti **provozním hodinám zařízení**, které
hlásí samo ve svém registru. Filtr se zanáší chodem ventilátoru, ne tím, že
plyne čas — u jednotky, která jede jednu směnu, by kalendářní trend lhal
dvojnásobně. Kalendářní odhad se z toho dopočítá podle toho, jakou část
sledovaného úseku jednotka skutečně běžela.

Kdyby počítadlo provozních hodin skočilo zpět (výměna regulátoru, restart
zařízení), vyhodnocení použije jen úsek od toho skoku dál. Jinak by proložená
přímka neznamenala nic.

### Příklady, co kontroly odhalí

```bash
python simulator.py --fault vzt1:stuck-valve   # topí i při povelu zavřít
python simulator.py --fault vzt3:sensor-fail   # čidlo přívodu hlásí -120 °C
```

Zaseklý ventil se pozná i tehdy, když regulace zavřít vůbec nepošle: topný
výkon je vyšší, než kolik daná poloha ventilu fyzikálně dovolí. Nerovnost
`topný výkon ≤ výkon ohřívače × povel na ventil` totiž platí vždy — teplota
topné vody může výkon jen srazit, nikdy zvednout.

Ovládání je obousměrné: posuvník zapíše žádanou hodnotu přes Modbus do zařízení
a ve schématu je hned vidět, jak na ni technologie zareagovala.

### Rozhraní serveru

| Cesta | Co dělá |
|---|---|
| `GET /` | vizualizace |
| `GET /api/meta` | soupis zařízení, veličin, jednotek, stavů a alarmů |
| `GET /api/state` | aktuální stav všech zařízení |
| `GET /api/history/{id}?keys=…` | posledních 15 minut pro trendy |
| `GET /api/diagnostics/{id}` | vyhodnocení provozu jednoho zařízení |
| `GET /api/energy` | energetická bilance a náklady |
| `GET /api/alarms` | aktivní alarmy (`scope=active`) nebo historie (`scope=history`) |
| `POST /api/alarms/ack` | kvitování — zapíše, kdo poruchu vzal na vědomí |
| `POST /api/alarms/reset` | odblokování poruchy zápisem do registru zařízení |
| `POST /api/write` | zápis žádané hodnoty `{device, key, value}` |
| `WS /ws` | stav celého závodu každou sekundu |

## Struktura

```
plant.py        soupis zařízení závodu — co kde stojí a na jakém portu
registers.py    mapy Modbus registrů všech typů zařízení
simulator.py    Modbus TCP server pro každé zařízení
poller.py       sběr dat ze všech zařízení do SQLite
diagnostics.py  vyhodnocení provozu z průběhu veličin
energy.py       energetická bilance, měrné ukazatele a náklady
alarmlog.py     záznamník alarmů s filtrací zákmitů a kvitováním
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

- vyhodnocení i pro chlazení a kotelnu: cyklování kompresorů a hořáků,
  approach věže proti projektu, nevyváženost motohodin ve dvojicích čerpadel
- spotřeby a náklady po měsících: plyn, elektřina, voda do věže

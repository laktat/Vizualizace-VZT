# Monitoring a diagnostika VZT jednotky přes Modbus TCP

Sběr provozních dat ze vzduchotechnické jednotky přes **Modbus TCP**, ukládání do
databáze a vyhodnocení nad nimi — predikce výměny filtru a **detekce dvou typických
poruch**: zaseklého topného ventilu a vadného čidla. Dashboard obsahuje **živý nákres
jednotky** s teplotami na čidlech a stavem filtru a umožňuje **za běhu měnit žádanou
teplotu a otáčky** (zápis zpět do jednotky přes Modbus).

Projekt vznikl z praxe. Jako servisní technik jezdím na VZT jednotky a vidím, že:
filtry se mění podle kalendáře, ne podle stavu; zaseklý topný ventil topí dál, i když
nemá; a vadné čidlo hlásí nesmysl, kterému regulace slepě věří. Jednotka přitom všechno
potřebné měří — jen se ta data nikam neukládají a nikdo je nevyhodnocuje.

## Teplotní řetězec jednotky

```
venku ──► rekuperátor ──► topný ohřívač (ventil) ──► přívod ──► místnost ──► odtah
  │             │                  │                    │                      │
t_outdoor  t_after_recup      valve_cmd            t_supply              t_extract
```

Regulace řídí na teplotu **místnosti** (čidlo v odtahu): dokud je místnost pod žádanou,
otevírá topný ventil; jakmile žádané dosáhne, pošle povel 0 %. Dvě veličiny pak prozradí
zaseklý ventil: **odtah** (drží se místnost nad žádanou i při povelu 0 %?) a **ohřev =
t_supply − t_after_recup** (přidává ohřívač teplo, i když má být zavřený?).

## Co to detekuje

**1) Zanášení filtru (predikce výměny).**
Tlaková ztráta filtru roste, jak se zanáší. Proložením přímkou se odhadne, kdy dosáhne
meze z dokumentace — kdy má technik přijet vyměnit filtr.

**2) Zaseklý / vadný topný ventil.**
Regulace řídí na teplotu **místnosti** (čidlo v odtahu, kde se z místnosti odsává). Když
místnost dosáhne žádané teploty, pošle na ventil 0 % — a od té chvíle se místnost nesmí
dál přehřívat. Detekce používá dvě nezávislé evidence:
- **místnost** (hlavní signál, odpovídá regulační logice): při zavřeném ventilu se místnost drží nad žádanou teplotou → pořád se topí;
- **ohřívač** (rychlé potvrzení): přívod je teplejší než vzduch za rekuperátorem, i když je ventil zavřený.

Signál z místnosti přesně sedí na to, jak regulace rozhoduje, ale je pomalý (tepelná
setrvačnost místnosti) a ovlivní ho i slunce nebo lidé. Signál z ohřívače je okamžitý a
lokalizuje poruchu na ventil, ale potřebuje spolehlivé čidlo za rekuperátorem. Proto obojí.
Kontrola má toleranci a vyžaduje víc vzorků se zavřeným ventilem, ne jeden šum.

**Pozn.:** poznávat poruchu jako „místnost se drží nad žádanou / stoupá" je správnější než
„místnost se nemění" — když se ventil zdravě zavře, přívodní vzduch je chladnější než
místnost, takže ta začne pomalu **klesat**. Hlídá se tedy přetápění, ne jakákoliv změna.

**3) Držení teploty místnosti.** Průběžná kontrola, jestli jednotka drží žádanou teplotu v místnosti.

**4) Vadné čidlo (věrohodnost).**
Každé čidlo má ve `registers.py` fyzikální rozsah (`valid_min`/`valid_max`). Když měří
mimo — typicky −120 °C u přerušeného čidla — označí se za nedůvěryhodné. Kontrola kouká
na posledních N vzorků, ne jen na poslední hodnotu: vadné čidlo často „bliká" a přerušovanou
poruchu by jediná hodnota minula.

## Nákres jednotky a živé ovládání

Dashboard (`app.py`) kreslí protiproudou VZT jednotku shora včetně potrubí (sání, přívod,
odtah, odpad), filtru, rekuperátoru, ohřívače s ventilem a obou ventilátorů. Do nákresu se
promítají živé teploty na čidlech, poloha topného ventilu a **stav filtru barvou** (zelená →
oranžová → červená podle zanesení). Vadné čidlo se v nákresu ukáže jako „CHYBA".

Posuvníky nahoře mění **žádanou teplotu místnosti** a **otáčky ventilátorů**. Hodnoty se
zapisují přes Modbus do holding registrů jednotky (`sp_room`, `sp_fan`), simulátor je čte
každý krok a reaguje: místnost jede za novou teplotou, otáčky se změní hned a **vyšší otáčky
zanášejí filtr rychleji** (víc protlačeného vzduchu). Dashboard se sám obnovuje každé 3 s.

To je přesně obousměrná BMS logika — čtení měření (input registry) i zápis žádaných hodnot
(holding registry).

## Simulace poruch

Simulátor umí naschvál vyrobit poruchu, aby bylo co vyhodnocovat. Tři způsoby spuštění:

```bash
python simulator.py                       # 1) zdravá jednotka — jen zanášení filtru
python simulator.py --fault stuck-valve   # 2) zaseklý topný ventil (topí při povelu 0 %)
python simulator.py --fault sensor-fail   # 3) čidlo přívodu hlásí -120 °C
```

`poller.py` a `streamlit run app.py` běží ve všech třech případech stejně — porucha se pozná
v diagnostice na dashboardu.

## Architektura

```
                 ┌──────── zápis žádaných hodnot (holding registry) ◄─────┐
                 ▼                                                         │
simulator.py ──Modbus TCP──► poller.py ──► data.sqlite ──► analysis.py ──► app.py
(jednotka                    (čtení        (historie)      (kontroly)   (nákres +
 + poruchy)                   měření)                                    ovládání)
```

Na reálné zakázce odpadá `simulator.py`, `poller.py` míří na IP skutečné jednotky a
adresy registrů se přepíšou ve `registers.py` podle dokumentace výrobce. Zbytek kódu
zůstává.

## Spuštění

```bash
pip install -r requirements.txt

python simulator.py      # 1. terminál — simulovaná jednotka (volitelně --fault ...)
python poller.py         # 2. terminál — čte a ukládá každých 5 s
streamlit run app.py     # 3. terminál — dashboard na http://localhost:8501
```

Necháš poller aspoň minutu sbírat. Pak `python analysis.py` vypíše všechny tři kontroly
do terminálu, dashboard je ukáže i s grafy.

Simulátor záměrně **zrychluje zanášení filtru** (~0,35 Pa/s), aby byl trend vidět během
minut. Na reálné jednotce jde o týdny až měsíce. Když měníš sadu registrů, smaž `data.sqlite`.

## Mapa registrů (input registers, funkce 4)

| Adresa | Veličina | Jednotka | Scale | Platný rozsah |
|---|---|---|---|---|
| 0 | Teplota venkovní (sání) | °C | 10 | −30 až 45 |
| 1 | Teplota za rekuperátorem | °C | 10 | −25 až 50 |
| 2 | Teplota za ohřívačem (přívod) | °C | 10 | 0 až 60 |
| 3 | Teplota odtahu (místnost) | °C | 10 | 5 až 40 |
| 4 | Povel na topný ventil | % | 10 | 0 až 100 |
| 5 | Tlaková ztráta filtru | Pa | 1 | 0 až 600 |
| 6 | Otáčky přívodního ventilátoru | % | 10 | 0 až 100 |
| 7 | Proud motoru | A | 100 | 0 až 20 |
| 8 | Provozní hodiny | h | 1 | 0 až 30000 |

*Scale* = kolikrát je hodnota v registru zvětšená proti fyzikální. Modbus posílá jen celá
čísla, takže 21,5 °C jede po drátě jako 215.

## Použité technologie

Python · pymodbus (čtení i zápis registrů) · SQLite · pandas · NumPy · Streamlit · SVG nákres

## Co dál

- [ ] Diagnostika ventilu závisí na čidle přívodu — když je čidlo označené za vadné, měl by se výsledek ventilu označit za nespolehlivý (závislost kontrol na sobě)
- [ ] Detekce podchlazení: povel na ventil je vysoký, ale přívod teplotu nedrží (zavzdušněný ohřívač, slabé čerpadlo)
- [ ] Detekce namrzání rekuperace podle teplotního spádu
- [ ] Zápis do holding registrů (změna žádané teploty z dashboardu)
- [ ] Nasazení jako systemd služba, běh 24/7
- [ ] Reálné měření na jednotce místo simulátoru

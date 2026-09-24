# QA report — dispečink závodu

Datum: 24. 9. 2026 · Testováno proti běžící aplikaci (simulátor, edge gateway,
MQTT broker, dispečink), ne proti podvrženým datům.

## Výsledek

**67 testů, všechny procházejí. Release gate: PROŠEL.**

| Sada | Testů | Co pokrývá |
|---|---|---|
| `test_api.py` | 13 | rozhraní odpovídá a odpovídá správně (obsah, ne jen stav 200) |
| `test_negative.py` | 32 | uživatel, který se snaží aplikaci rozbít |
| `test_e2e.py` | 13 | kritické cesty uživatele přes rozhraní (Playwright) |
| `test_release_gate.py` | 9 | smí to jít do provozu? |

Spuštění: `pytest tests/` — aplikace musí běžet, jinak se testy přeskočí
s vysvětlením.

Existující testy v repu nebyly žádné; tahle sada je první.

## Nalezené a opravené chyby

Deset chyb v aplikaci. Nejzávažnější dvě nejsou ty, které by šly uhodnout
z kódu — vyplavaly z běhu.

### 1. Neúplný obraz žádaných hodnot (závažná)

Žádané hodnoty se na sběrnici zpráv publikovaly **jen při změně** a s příznakem
*retained*. Zapamatovaná zpráva je ale vždy jen ta poslední, takže nově
připojený dispečink dostal jedinou hodnotu a o zbytku se nedozvěděl, dokud se
náhodou nezměnila.

Projev: VZT 1 měla ve stavu 7 žádaných hodnot z 8, chyběla `fault_sim`.
Zkušební panel proto neukázal nasazenou poruchu a posuvník neměl co zobrazit.

Oprava: žádané hodnoty se posílají celé (je jich pár a mění se zřídka).
Dispečink navíc kontroluje úplnost i u nich, ne jen u měřených, a dokud obraz
není úplný, přečte si zařízení sám.

### 2. Navigace přes adresu nefungovala (závažná)

Aplikace čte `#hash` jen při startu a na jeho změnu nereagovala. Tlačítko
Zpět v prohlížeči ani odkaz na konkrétní obrazovku vložený do adresního
řádku nedělaly nic.

Oprava: přepínání jde přes změnu hashe a posluchač `hashchange`.

### 3. Seznam alarmů se přestavoval každou sekundu

Stav přichází každou sekundu a seznamy se z něj skládaly znovu. Tlačítko,
na které operátor míří, tím zmizelo a vzniklo nové — kliknutí propadlo.
Chytil to E2E test, kde klikání opakovaně vypršelo.

Oprava: obsah se přepisuje jen tehdy, když se opravdu změnil.

### 4. NaN se tiše stal nejvyšší žádanou hodnotou

Porovnání s NaN je vždy nepravdivé, takže omezení do mezí vrátilo mez.
Poslat regulaci topení NaN znamenalo nastavit maximum.

### 5. Nesmyslný index poruchy nasadil jinou poruchu

Požadavek na zkušební poruchu číslo 99 se utnul na nejvyšší platnou, což je
**jiná skutečná porucha** (výpadek komunikace), a volající dostal 200, jako
by se stalo to, co chtěl.

Oprava k 4 a 5: hodnoty mimo meze se odmítají s vysvětlením. Driver je dál
omezuje jako poslední pojistka — omezení chrání technologii, ale nesmí
předstírat, že se stalo něco jiného.

### 6. Prázdné uložení smazalo profil režimu

`POST /api/modes/save` bez pole `values` bralo chybějící pole jako prázdný
profil a **smazalo uložené nastavení režimu**. Ztráta dat malformovaným
požadavkem.

### 7. Chybějící `enabled` vypnulo automatiku

`POST /api/healing` bez `enabled` se bralo jako `False`, takže malformovaný
požadavek tiše vypnul automatiku zasahující do technologie. Vypnutí musí být
vždy výslovné.

### 8. Překlep ve `scope` tiše vrátil jiná data

`/api/alarms?scope=nesmysl` vrátilo historii místo aktivních alarmů.

### 9. Negativní limit vrátil celou knihu

SQLite bere negativní `LIMIT` jako „bez omezení“, takže `limit=-5` vracelo
všechno.

### 10. Chybové zprávy vypisovaly vnitřnosti

Do odpovědí prosakoval text výjimky včetně uvozovek z `KeyError`.
Nahrazeno srozumitelnými hláškami; profil režimu navíc neprijme cizí
zařízení ani cizí klíč.

## Co bylo v pořádku

- **Obě sběrnice**: 11/11 zařízení odpovídá, VZT 3 po BACnet/IP dodává
  stejný tvar dat jako modbusová zařízení.
- **Zápis a kvitování** projdou až do zařízení, po Modbusu i po BACnetu.
- **Přepnutí režimu** přenastaví 47 žádaných hodnot.
- **Energetická bilance** souhlasí: součet položek se rovná celku, podíly
  dávají 100 %.
- **SQL v názvu zařízení, průchod cestou mimo aplikaci, 5 000 znaků v poli
  jména** — odmítnuto nebo bezpečně zkráceno.
- **Konzole prohlížeče je čistá** na všech deseti obrazovkách.
- **Logy** neobsahují výjimku kromě odpovědí umlčeného zařízení při testu
  výpadku komunikace, což je správné chování.

## Co testy nepokrývají

- **Dlouhodobý provoz.** Chyba s neúplnými žádanými hodnotami se projevila
  až po restartu dispečinku za běžícího gatewaye; podobné časové souběhy
  sada neumí vynutit.
- **Souběh více operátorů.** Dva lidé zapisující do stejného zařízení
  současně se netestují.
- **Skutečná technologie.** Vše běží proti simulaci. Modbus a BACnet jsou
  reálné protokoly na reálných portech, ale zařízení za nimi jsou model.
- **Model chodu ventilátoru** má vlastní měřenou mez popsanou v README:
  je to síto, ne detektor, a testy hlídají jen to, že nikdy nedá úroveň
  „k řešení“ a nemůže tedy řídit korekce.

## Dvě chyby v testech, ne v aplikaci

Pro pořádek, ať je vidět rozdíl:

- `inner_text()` na SVG prvcích neexistuje; většina hodnot sedí v SVG.
  Nahrazeno `text_content()`.
- Test kvitování počítal s tím, že odstranění příčiny alarm zhasne. Nezhasne
  — porucha se v zařízení **zapamatuje** a drží, dokud ji někdo nekvituje.
  To je záměr, takže se úklid před testem musel projít stejnou cestou jako
  obsluha v poli.

"""
Detekce anomálií ve chodu ventilátoru (IsolationForest).

K čemu to je: pravidlová diagnostika hlídá věci, které někdo dopředu popsal —
zanesený filtr, netěsný ventil, vadné čidlo. Neumí ale říct "tohle vypadá
jinak než obvykle, a nevím proč". Přesně to má na starosti model: ne nahradit
pravidla, ale ukázat člověku kombinace hodnot, na které se zapomnělo.

CO SE MODELU PŘEDKLÁDÁ

Surové otáčky, proud a tlak nestačí — ty jen kódují pracovní bod. Ventilátor
na 40 % by proti trénovacím datům ze 78 % vypadal jako anomálie, i když je
úplně v pořádku. Model proto dostává vztahy, které na pracovním bodě
nezávisí a při závadě se rozejdou:

    příkon / (otáčky)³      ze zákona afinity konstanta; utekne při
                            mechanickém odporu, vadném ložisku, brzdícím rotoru
    proud / otáčky          totéž z elektrické strany
    průtok / otáčky         konstanta, dokud se cesta nepřiškrtí

Zkoušel jsem k nim přidat i odpor cesty jako dp/průtok², což je obvyklý
ukazatel zanesení. Nefunguje to: tlaková ztráta filtru se tady odvíjí od
provozních hodin, ne od průtoku, takže se ten podíl mění čistě s otáčkami
a jednotka na 45 % dostávala skóre 90. Příznak, který má být na pracovním
bodě nezávislý, musí být nezávislý i na datech, která dostane — ne jen
v učebnici.

SUROVÉ HODNOTY MEZI PŘÍZNAKY NEJSOU, a to schválně. Vyzkoušel jsem obojí a
obojí dělalo falešné poplachy:

  * surové otáčky — jednotka na 45 % dostala skóre 100 jen proto, že se
    takové otáčky v učení nevyskytly;
  * surová tlaková ztráta — zdravý provoz se zanesením mimo naučený rozsah
    skóroval stejně jako skutečná závada.

Pracovní bod ani pomalý drift nesmí být anomálie. Anomálie je, když se od
pracovního bodu rozejde to, co na něm nemá záležet.

Model tím pádem hlídá ELEKTROMECHANICKOU stranu, kde jsou pravidla nejslabší
(na opotřebené ložisko nebo brzdící rotor nikdo pravidlo nenapsal).
Vzduchovou stranu — zanesený filtr, přiškrcená cesta — pokrývají pravidla
v diagnostics.py, a ta to umí pojmenovat, což model neumí.

ČÍM SI TENHLE MODUL NEHRAJE

Neutrénovaný model vydává nesmysly, proto dokud nemá dost vzorků, skóre
nevydá vůbec a řekne, že sbírá data. Učí se jen z chodu nad prahem otáček —
stojící jednotka není anomálie, jen stojí.

MĚŘENÁ MEZ TÉHLE METODY, ať se na ni nikdo nespoléhá víc, než unese:

Na zkoušce se NEPODAŘILO oddělit zdravý provoz od závad tak, aby mezi nimi
byla mezera. Zdravý provoz se zanesením mimo naučený rozsah skóroval 45,
skutečné závady 41 až 57. Příčina je věcná, ne v ladění: průtok na otáčku
klesá se zanášením filtru úplně legitimně, takže model nemá jak rozlišit
"průtok klesl, protože se filtr za měsíce zanesl" od "průtok klesl, protože
se zavřela klapka". S kontextem tlakové ztráty to nejde (falešné poplachy na
nenaučeném rozsahu), bez něj taky ne.

Z toho plyne, co tenhle modul je a co není: je to SÍTO, které ukáže "koukni
se sem", ne detektor závad. Proto nikdy nedá úroveň "k řešení" a proto na
něj nesmí sahat automatické korekce.

Kdyby to mělo být detekce, tahle cesta na to nestačí. Správně by se měřené
hodnoty porovnávaly s PŘEDPOVĚDÍ fyzikálního modelu a hlídal se rozdíl —
u technologie se známou fyzikou to funguje nepoměrně lépe než hledání
odlehlých bodů bez modelu.

A pozor i na to, že model je naučený na simulaci. Fyzika je v ní hladká a
šumu má málo, takže se tady chová lépe, než jak by si vedl na skutečné
technologii.
"""

import pickle
from collections import deque
from pathlib import Path

MIN_FAN = 30.0            # pod tímhle se neučí ani neskóruje [%]
MIN_FLOW = 100.0          # [m³/h]
MIN_SAMPLES = 200         # kolik vzorků chce model, než něco řekne
WINDOW = 3000             # kolik vzorků se drží pro učení
RETRAIN_EVERY = 900       # po kolika nových vzorcích se model přeučí

# Násobek, kterým se roztahuje škála skóre: sto odpovídá tolikanásobku
# vzdálenosti nejkrajnějšího trénovacího vzorku. Vybráno měřením — vyšší
# hodnoty srazí i skutečné závady pod hranici hlášení, nižší dělají falešné
# poplachy na zdravém provozu.
CALIBRATION = 1.3

STORE = Path(__file__).parent / "models"


def features(values):
    """
    Spočítá příznaky z jednoho odečtu. Vrací None, když jednotka neběží
    dost na to, aby měly vztahy smysl.
    """
    fan = float(values.get("fan_supply", 0.0))
    flow = float(values.get("flow_supply", 0.0))
    if fan < MIN_FAN or flow < MIN_FLOW:
        return None

    ratio = fan / 100.0
    dp = float(values.get("filter_dp_sup", 0.0))
    power = float(values.get("power", 0.0))
    current = float(values.get("current", 0.0))
    return [
        power / (ratio ** 3),            # zákon afinity: konstanta
        current / ratio,                 # totéž z elektrické strany
        flow / ratio,                    # klesá se zanášením, jinak konstanta
    ]


FEATURE_NAMES = ("příkon/otáčky³", "proud/otáčky", "průtok/otáčky")


class FanAnomaly:
    """
    Model pro jeden ventilátor.

    Každá jednotka má svůj: VZT 1 tlačí 45 000 m³/h a VZT 3 dvanáct tisíc,
    takže jeden společný model by se učil hlavně rozdíly mezi jednotkami
    místo odchylek od jejich vlastního normálu.
    """

    def __init__(self, device_id):
        self.device_id = device_id
        self.samples = deque(maxlen=WINDOW)
        self.model = None
        self.center = 0.0         # medián zdravého provozu
        self.edge = -0.1          # hranice, za kterou už je to nezvyklé
        self.trained_on = 0
        self.since_train = 0

    # -- učení ---------------------------------------------------------------
    def observe(self, values):
        """Přidá odečet do zásoby pro učení."""
        row = features(values)
        if row is None:
            return False
        self.samples.append(row)
        self.since_train += 1
        if len(self.samples) >= MIN_SAMPLES and (
                self.model is None or self.since_train >= RETRAIN_EVERY):
            self.train()
        return True

    def train(self):
        """
        Naučí model na dosud naměřeném provozu.

        Contamination je nastavená nízko: trénovací data jsou z běžného
        provozu, kde je odchylek málo. Kdyby se čekalo procento, model by
        si za anomálie začal vybírat normální krajní stavy.
        """
        from sklearn.ensemble import IsolationForest

        data = list(self.samples)
        if len(data) < MIN_SAMPLES:
            return False
        model = IsolationForest(n_estimators=150, contamination=0.01,
                                random_state=0)
        model.fit(data)

        # Kalibrace, aby skóre mělo čitelný rozsah. Surová hodnota z
        # decision_function nic neříká; tady se z ní udělá 0 až 100 podle
        # toho, jak daleko je odečet od zdravého provozu.
        #
        # Sto odpovídá DVOJNÁSOBKU vzdálenosti nejkrajnějšího trénovacího
        # vzorku, ne jeho percentilu. Napoprvé jsem bral první percentil a
        # dopadlo to špatně: v simulaci je šumu málo, zdravý oblak je proto
        # velmi těsný a každý bod kousek za jeho okrajem vysytil skóre na
        # sto, aniž by se cokoli skutečně dělo.
        scores = sorted(model.decision_function(data))
        self.center = scores[len(scores) // 2]
        self.edge = self.center - CALIBRATION * (self.center - scores[0])
        self.model = model
        self.trained_on = len(data)
        self.since_train = 0
        self.save()
        return True

    # -- skórování ------------------------------------------------------------
    def score(self, values):
        """
        Vrátí (skóre 0–100, vysvětlení) nebo (None, důvod).

        Sto znamená "tak nezvyklé, jak jen v trénovacích datech bylo",
        nula "úplně obvyklé". Není to pravděpodobnost závady.
        """
        if self.model is None:
            need = max(0, MIN_SAMPLES - len(self.samples))
            return None, (f"model se učí, chybí {need} vzorků"
                          if need else "model se učí")
        row = features(values)
        if row is None:
            return None, "jednotka neběží, není co porovnávat"

        raw = float(self.model.decision_function([row])[0])
        span = self.center - self.edge
        score = 0.0 if span <= 1e-9 else (self.center - raw) / span * 100.0
        return max(0.0, min(100.0, score)), self.explain(row)

    def attributable(self, values):
        """
        Dá se vysoké skóre přičíst konkrétnímu vztahu?

        Když model křičí, ale neumí ukázat na nic konkrétního, je to spíš
        jeho vlastní nejistota než závada na stroji — a takové hlášení
        operátora jen otupí. Skóre se pak ukáže, ale nezvedá se kvůli němu
        poplach.
        """
        row = features(values)
        return bool(row and self.explain(row))

    def explain(self, row):
        """
        Který příznak je nejdál od toho, co model viděl.

        Skóre samo o sobě je k ničemu — operátor potřebuje vědět, čeho si
        má všimnout. Bere se příznak s největší odchylkou od mediánu
        trénovacích dat, vyjádřenou v jeho vlastním rozptylu.
        """
        data = list(self.samples)
        if not data:
            return ""
        worst, worst_z = None, 0.0
        for i, name in enumerate(FEATURE_NAMES):
            column = sorted(s[i] for s in data)
            median = column[len(column) // 2]
            spread = column[int(len(column) * 0.84)] - median
            if spread <= 1e-9:
                continue
            z = abs(row[i] - median) / spread
            if z > worst_z:
                worst, worst_z = name, z
        if worst is None or worst_z < 2.0:
            return ""
        return f"nejvíc se vymyká {worst}"

    # -- uložení --------------------------------------------------------------
    def path(self):
        return STORE / f"fan_{self.device_id}.pkl"

    def save(self):
        try:
            STORE.mkdir(exist_ok=True)
            with open(self.path(), "wb") as fh:
                pickle.dump({"model": self.model, "center": self.center,
                             "edge": self.edge, "samples": list(self.samples)}, fh)
        except Exception:
            pass      # model je pohodlí, ne podmínka provozu

    def load(self):
        try:
            with open(self.path(), "rb") as fh:
                data = pickle.load(fh)
            self.model = data["model"]
            self.center = data["center"]
            self.edge = data["edge"]
            self.samples.extend(data.get("samples", []))
            self.trained_on = len(self.samples)
            return True
        except Exception:
            return False

    def status(self):
        return {"device": self.device_id, "trained": self.model is not None,
                "samples": len(self.samples), "trained_on": self.trained_on}


class AnomalyDetector:
    """
    Modely všech ventilátorů pohromadě.

    Data dostává odjinud — z dispečinku, který je má ze sběrnice zpráv.
    Stejně dobře by si mohl tenhle modul sedět ve vlastním procesu a odebírat
    si MQTT sám; od toho je publish/subscribe, aby další konzument nikoho
    neobtěžoval. Tady je uvnitř dispečinku proto, že skóre má skončit ve
    výstupu diagnostiky, a ta je jeho.
    """

    def __init__(self, device_ids):
        self.fans = {}
        for dev_id in device_ids:
            fan = FanAnomaly(dev_id)
            fan.load()
            self.fans[dev_id] = fan

    def observe(self, device_id, values):
        fan = self.fans.get(device_id)
        return fan.observe(values) if fan else False

    def score(self, device_id, values):
        fan = self.fans.get(device_id)
        return fan.score(values) if fan else (None, "model není")

    def status(self):
        return [fan.status() for fan in self.fans.values()]

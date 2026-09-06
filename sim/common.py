"""
Stavební kameny sdílené všemi modely technologie.

Simulace běží ve zrychleném čase: jeden reálný krok (1 s) znamená SIM_SPEED
sekund provozu. Díky tomu je za pár minut sledování vidět denní cyklus,
střídání čerpadel i přírůstek motohodin.
"""

import math
import random

# --- fyzikální konstanty ------------------------------------------------------
CP_WATER = 4.186        # měrná tepelná kapacita vody [kJ/(kg·K)]
RHO_WATER = 1000.0      # hustota vody [kg/m³]
CP_AIR = 1.006          # měrná tepelná kapacita vzduchu [kJ/(kg·K)]
RHO_AIR = 1.2           # hustota vzduchu [kg/m³]

SIM_SPEED = 60.0        # kolikrát rychleji než realita (přepisuje simulator.py)


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def lag(current, target, dt, tau):
    """Setrvačnost prvního řádu — hodnota se plynule blíží cíli s časovou konstantou tau."""
    if tau <= 0:
        return target
    return current + (target - current) * (1.0 - math.exp(-dt / tau))


def water_kw(flow_m3h, dt_k):
    """Tepelný výkon přenesený vodou [kW] z průtoku [m³/h] a ochlazení [K]."""
    return flow_m3h / 3600.0 * RHO_WATER * CP_WATER * dt_k


def air_kw(flow_m3h, dt_k):
    """Tepelný výkon přenesený vzduchem [kW]."""
    return flow_m3h / 3600.0 * RHO_AIR * CP_AIR * dt_k


def wet_bulb(t_db, rh):
    """Teplota mokrého teploměru [°C] (Stullova aproximace)."""
    rh = clamp(rh, 5.0, 100.0)
    return (t_db * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(t_db + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def sat_pressure(t):
    """
    Tlak sytých par chladiva R410A [bar abs] podle teploty [°C].

    Antoineova křivka proložená tabulkovými body chladiva:
    5 °C -> 9,3 bar, 25 °C -> 16,5 bar, 45 °C -> 27,3 bar.
    """
    return math.exp(10.796 - 2382.7 / clamp(t + 273.15, 200.0, 350.0))


def set_bit(word, bit, on):
    return (word | (1 << bit)) if on else (word & ~(1 << bit))


def noise(sigma):
    return random.gauss(0.0, sigma)


class PI:
    """PI regulátor s omezením výstupu a ochranou proti přeregulování integrálu."""

    def __init__(self, kp, ti, lo=0.0, hi=100.0, out=0.0):
        self.kp, self.ti, self.lo, self.hi = kp, ti, lo, hi
        self.i = out

    def step(self, error, dt):
        p = self.kp * error
        if self.ti > 0:
            self.i += self.kp / self.ti * error * dt
        self.i = clamp(self.i, self.lo - abs(p), self.hi + abs(p))
        out = clamp(p + self.i, self.lo, self.hi)
        # anti-windup: integrál nedržíme za hranicí, na které výstup stojí
        self.i = clamp(self.i, self.lo - p, self.hi - p)
        return out


# --- stavy (shodné se slovníky v registers.py) -------------------------------
STOP, START, RUN, STANDBY_OR_LOCK, FAULT = 0, 1, 2, 3, 4


class Motor:
    """
    Společný model točivého stroje — ventilátor, čerpadlo, kompresor.

    Hlídá minimální dobu chodu a pauzy (kvůli tepelné ochraně motoru),
    počítá motohodiny a starty, umí simulovat poruchu. Rozběh a doběh
    otáček má setrvačnost, takže se ve vizualizaci dá animovat.

    PORUCHA SE ZAPAMATUJE. Skutečný stroj se po poruše sám nerozjede, ani
    když příčina zmizí — motorová ochrana zůstane vyhozená, dokud ji někdo
    nekvituje. Proto se rozlišuje:

        fault   vnější příčina (přetížení, výpadek fáze, zaseklý rotor)
        latched zapamatovaná porucha, která čeká na kvitování

    Kvitování zapomene poruchu. Když příčina pořád trvá, naskočí okamžitě
    znovu — přesně jako v poli, kde kvitování bez odstranění závady nepomůže.
    """

    def __init__(self, name, hours=0.0, starts=0, min_run=180.0, min_stop=180.0,
                 spin_up=6.0, rated_kw=1.0):
        self.name = name
        self.hours = hours          # motohodiny [h]
        self.starts = starts
        self.min_run = min_run      # minimální doba chodu [s simulovaného času]
        self.min_stop = min_stop
        self.spin_up = spin_up      # časová konstanta rozběhu otáček [s]
        self.rated_kw = rated_kw
        self.state = STOP
        self.speed = 0.0            # skutečné otáčky [%]
        self.timer = 1e9            # jak dlouho je v aktuálním stavu [s]
        self.fault = False          # vnější příčina poruchy
        self.latched = False        # zapamatovaná porucha, čeká na kvitování

    def trip(self):
        """Vyhodí ochranu — stroj stojí, dokud poruchu někdo nekvituje."""
        self.latched = True

    def reset(self):
        """Kvitování poruchy. Když příčina trvá, porucha naskočí znovu."""
        self.latched = False

    @property
    def faulty(self):
        """Stroj je k dispozici, jen když nemá poruchu ani příčinu."""
        return self.fault or self.latched

    @property
    def running(self):
        return self.state in (START, RUN)

    @property
    def stopped(self):
        """Stojí — ať už úplně odstavený, nebo připravený jako záloha."""
        return self.state in (STOP, STANDBY_OR_LOCK)

    def can_start(self):
        return not self.faulty and self.stopped and self.timer >= self.min_stop

    def can_stop(self):
        return self.state != STOP and self.timer >= self.min_run

    def step(self, dt, demand_on, demand_speed=100.0):
        """demand_on = povel od nadřazené regulace, demand_speed = žádané otáčky [%]."""
        self.timer += dt

        if self.fault:
            self.latched = True          # trvající příčina drží poruchu

        if self.latched:
            if self.state != FAULT:
                self.state, self.timer = FAULT, 0.0
            self.speed = lag(self.speed, 0.0, dt, self.spin_up)
            return self.speed

        if self.state == FAULT:          # porucha byla kvitována a příčina zmizela
            self.state, self.timer, self.speed = STOP, 0.0, 0.0

        if demand_on and self.stopped and self.timer >= self.min_stop:
            self.state, self.timer = START, 0.0
            self.starts += 1
        elif not demand_on and self.running and self.timer >= self.min_run:
            self.state, self.timer = STOP, 0.0
        elif self.state == START and self.speed > 0.9 * demand_speed:
            self.state, self.timer = RUN, 0.0

        target = clamp(demand_speed, 0.0, 100.0) if self.running else 0.0
        self.speed = lag(self.speed, target, dt, self.spin_up)
        if self.running:
            self.hours += dt / 3600.0
        return self.speed

    def power_kw(self):
        """Příkon podle zákona afinity — u čerpadel a ventilátorů třetí mocnina otáček."""
        return self.rated_kw * (self.speed / 100.0) ** 3


class DutyStandby:
    """
    Dvojice čerpadel v zapojení provoz/záloha.

    Vedoucí čerpadlo se po nastaveném počtu provozních hodin vystřídá se
    záložním, aby se opotřebovávala rovnoměrně. Když vedoucí odejde do
    poruchy, záloha naskočí okamžitě.
    """

    def __init__(self, a: Motor, b: Motor, changeover_h=24.0):
        self.pumps = (a, b)
        self.lead = 0                 # index vedoucího čerpadla
        self.changeover_h = changeover_h
        self.since_change = 0.0       # doba od posledního střídání [h]

    def step(self, dt, demand_on, speed, changeover_h=None):
        if changeover_h:
            self.changeover_h = changeover_h
        self.since_change += dt / 3600.0

        lead, standby = self.pumps[self.lead], self.pumps[1 - self.lead]

        # 1) porucha vedoucího -> okamžité převzetí zálohou
        if lead.faulty and not standby.faulty:
            self.lead ^= 1
            self.since_change = 0.0
            lead, standby = standby, lead

        # 2) pravidelné střídání, jen když zrovna oba mohou přepnout
        elif (demand_on and self.since_change >= self.changeover_h
                and lead.can_stop() and standby.can_start()):
            self.lead ^= 1
            self.since_change = 0.0
            lead, standby = standby, lead

        lead.step(dt, demand_on and not lead.faulty, speed)
        standby.step(dt, False, 0.0)
        # záloha se ve vizualizaci hlásí jako "Záloha", ne jako "Stop",
        # aby bylo poznat, že je připravená naskočit
        if standby.stopped and not standby.faulty:
            standby.state = STANDBY_OR_LOCK if demand_on else STOP
        return lead

    def running_speed(self):
        return max(p.speed for p in self.pumps)

    def reset(self):
        for p in self.pumps:
            p.reset()


class Sequencer:
    """
    Postupné najíždění více stejných strojů (chillery, kotle, kompresory).

    Nabíhá se vždy stroj s nejmenším počtem motohodin, odstavuje se ten
    s největším — provoz se tak rozloží rovnoměrně.
    """

    @staticmethod
    def pick_start(machines):
        avail = [m for m in machines if m.can_start()]
        return min(avail, key=lambda m: m.hours) if avail else None

    @staticmethod
    def pick_stop(machines):
        avail = [m for m in machines if m.running and m.can_stop()]
        return max(avail, key=lambda m: m.hours) if avail else None

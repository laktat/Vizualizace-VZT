"""
Model plynového kotle s modulovaným hořákem.

Hořák nejede plynule od nuly — má minimální modulaci (typicky 25 % výkonu).
Když je potřeba menší výkon, kotel musí cyklovat: zapálí, natopí, zhasne.
Časté cyklování je nešvar, který je na motohodinách a počtu startů vidět,
proto model počítá obojí zvlášť.

Startovní sekvence odpovídá skutečnosti:
    Stop -> předvětrání -> zapalování -> hoří

Když zápal nevyjde, hořák se ZABLOKUJE a sám se už nerozjede — přesně proto
se do kotelen chodí kvitovat poruchy. Zablokování zruší až zápis do
kvitovacího registru; kotel pak zápal zkusí znovu a obvykle uspěje.
"""

import random

from .common import PI, clamp, lag, noise, set_bit, water_kw

B_OFF, B_PURGE, B_IGNITE, B_FLAME, B_FAULT = 0, 1, 2, 3, 4
PURGE_TIME = 30.0        # předvětrání spalovací komory [s]
IGNITE_TIME = 8.0
MIN_RUN = 300.0          # minimální doba hoření [s] — ochrana proti cyklování
MIN_OFF = 180.0          # minimální pauza mezi zapálením [s]


class Boiler:
    def __init__(self, dev):
        p = dev.params
        self.dev = dev
        self.power_kw = p["power_kw"]
        self.min_mod = p["min_mod"]

        self.t_flow = 45.0
        self.t_return = 40.0
        self.t_flue = 25.0
        self.modulation = 0.0
        self.burner = B_OFF
        self.timer = 1e6
        self.hours = float(p.get("run_hours", 0.0))
        self.starts = int(p.get("starts", 0))
        self.gas_total = 0.0
        self.gas_energy = self.heat_energy = 0.0
        self.pressure = 2.1
        self.pi = PI(kp=14.0, ti=150.0, lo=0.0, hi=100.0)
        # jak často zápal nevyjde — u zdravého hořáku výjimečně
        self.ignition_fail = p.get("ignition_fail", 0.03)
        self.lockout = False          # zablokovaný hořák, čeká na kvitování
        self.faults = set()

    def reset(self):
        """Kvitování poruchy hořáku — zruší zablokování a dovolí nový zápal."""
        self.lockout = False
        if self.burner == B_FAULT and "burner-fault" not in self.faults:
            self.burner, self.timer = B_OFF, 0.0

    @property
    def available(self):
        """Kotel je pro kaskádu k dispozici, jen když není v poruše."""
        return not self.lockout and "burner-fault" not in self.faults

    def set_fault(self, name, on=True):
        (self.faults.add if on else self.faults.discard)(name)

    @property
    def firing(self):
        return self.burner == B_FLAME

    def step(self, dt, hold, demand_on, t_return, flow):
        """demand_on = povel z nadřazené regulace kotelny (kaskáda kotlů)."""
        enable = hold["enable"] > 0.5 and demand_on
        self.timer += dt
        sp = hold["sp_flow"]
        min_mod = max(hold["mod_min"], self.min_mod * 100.0)

        # --- startovní sekvence hořáku ----------------------------------------
        # Hořák nesmí často spínat: jednou zapálený musí hořet aspoň MIN_RUN
        # a po zhasnutí čekat MIN_OFF. Bez těchhle prodlev by kotel na malé
        # zátěži cykloval po desítkách startů za hodinu a hořák to odnese.
        if "burner-fault" in self.faults:
            self.burner, self.lockout = B_FAULT, True
        elif self.lockout:
            self.burner = B_FAULT
        elif self.burner == B_FAULT:
            self.burner, self.timer = B_OFF, 0.0
        elif self.burner == B_OFF:
            if enable and self.t_flow < sp - 3.0 and self.timer >= MIN_OFF:
                self.burner, self.timer = B_PURGE, 0.0
        elif self.burner == B_PURGE:
            if not enable:
                self.burner, self.timer = B_OFF, 0.0
            elif self.timer >= PURGE_TIME:
                self.burner, self.timer = B_IGNITE, 0.0
        elif self.burner == B_IGNITE and self.timer >= IGNITE_TIME:
            if random.random() < self.ignition_fail:
                # zápal nevyšel — hořák se zablokuje a čeká na kvitování
                self.burner, self.timer, self.lockout = B_FAULT, 0.0, True
            else:
                self.burner, self.timer = B_FLAME, 0.0
                self.starts += 1
        elif self.burner == B_FLAME and self.timer >= MIN_RUN:
            # kotel zhasne, až je natopeno nebo odejde povel z kaskády
            if not enable or self.t_flow > sp + 5.0:
                self.burner, self.timer = B_OFF, 0.0

        # --- modulace výkonu ---------------------------------------------------
        want = self.pi.step(sp - self.t_flow, dt) if enable else 0.0
        target_mod = clamp(want, min_mod, 100.0) if self.firing else 0.0
        self.modulation = lag(self.modulation, target_mod, dt, 12.0)
        power = self.power_kw * self.modulation / 100.0
        if self.firing:
            self.hours += dt / 3600.0

        # --- teplota vody a spalin ---------------------------------------------
        if flow > 0.5:
            rise = power / max(water_kw(flow, 1.0), 0.01)
            self.t_flow = lag(self.t_flow, t_return + clamp(rise, 0.0, 45.0), dt, 35.0)
        elif power > 0:
            # hoří bez průtoku — voda se v kotli přehřívá, od toho je
            # havarijní termostat
            self.t_flow = lag(self.t_flow, self.t_flow + power * 0.05, dt, 20.0)
        else:
            # odstavený kotel bez průtoku pomalu chladne do kotelny
            self.t_flow = lag(self.t_flow, max(t_return, 20.0), dt, 3600.0)
        self.t_return = lag(self.t_return, t_return, dt, 25.0)
        # kondenzační kotel odchází se spalinami jen mírně nad zpátečkou
        self.t_flue = lag(self.t_flue,
                          self.t_return + 5.0 + 0.25 * self.modulation if self.firing
                          else max(20.0, self.t_return - 5.0), dt, 30.0)

        # --- účinnost a spotřeba plynu ------------------------------------------
        # kondenzační kotel je tím účinnější, čím studenější je zpátečka
        efficiency = clamp(104.0 - max(self.t_return - 35.0, 0.0) * 0.55, 86.0, 104.0)
        gas_flow = power / 9.97 / (efficiency / 100.0) if power > 0 else 0.0   # m³/h
        self.gas_total += gas_flow * dt / 3600.0
        # energie v plynu se počítá z výhřevnosti 9,97 kWh/m³
        self.gas_energy += gas_flow * 9.97 * dt / 3600.0
        self.heat_energy += power * dt / 3600.0
        # tlak drží expanzní nádoba; s teplotou vody voda expanduje a tlak roste
        self.p_cold = clamp(getattr(self, "p_cold", 1.9) - 2.0e-6 * dt / 60.0, 0.9, 2.4)
        self.pressure = clamp(self.p_cold + (self.t_flow - 30.0) * 0.012, 0.5, 3.2)

        a = 0
        a = set_bit(a, 0, self.lockout or "burner-fault" in self.faults)
        a = set_bit(a, 1, self.t_flow > 95.0)
        a = set_bit(a, 2, self.pressure < 1.0)
        a = set_bit(a, 3, self.firing and flow < 0.25 * self.dev.params["flow_nom"])
        a = set_bit(a, 4, self.t_flue > 200.0)
        a = set_bit(a, 5, enable and self.t_flow < sp - 8.0 and self.modulation > 95.0)

        return {
            "t_flow": self.t_flow + noise(0.05), "t_return": self.t_return,
            "t_flue": self.t_flue, "modulation": self.modulation,
            "burner": self.burner,
            "flame": 88.0 + noise(2.0) if self.firing else 0.0,
            "pressure": self.pressure, "flow": flow, "power": power,
            "gas_flow": gas_flow, "gas_total": self.gas_total,
            "efficiency": efficiency if self.firing else 0.0,
            "state": self.burner, "alarms": a,
            "run_hours": self.hours, "starts": self.starts,
            "gas_energy": self.gas_energy, "heat_energy": self.heat_energy,
            "_heat_kw": power,
        }

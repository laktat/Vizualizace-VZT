"""
Model chladicí věže se dvěma ventilátory.

Věž odvádí teplo z kondenzátorů chillerů do venkovního vzduchu odparem vody.
Fyzikální mez je teplota MOKRÉHO teploměru — pod ni se voda ochladit nedá.
Rozdíl mezi teplotou vody z věže a mokrým teploměrem se jmenuje approach
a je to hlavní ukazatel, jestli věž stíhá:

    velký approach + ventilátory na 100 % = věž je na hranici (zanesená
    výplň, horký vlhký den), chillery pojedou s horším COP

Voda se odparem ztrácí a zahušťuje, proto model počítá i hladinu v bazénu,
dopouštění a odluh podle vodivosti.
"""

from .common import (
    Motor, PI, clamp, lag, noise, set_bit, water_kw, wet_bulb, FAULT,
)

APPROACH_MIN = 2.8       # nejmenší dosažitelný approach [K]
EVAP_FRACTION = 0.0016   # odpar vody na kW odvedeného výkonu [m³/h/kW]


class Tower:
    def __init__(self, dev):
        p = dev.params
        self.dev = dev
        self.reject_nom = p["reject_kw"]
        self.flow_nom = p["flow_nom"]
        self.basin_m3 = p["basin_m3"]

        self.fans = [
            # ventilátor věže spotřebuje asi 1,7 % odvedeného výkonu,
            # u 900kW věže tedy dvakrát 7,5 kW
            Motor(f"Ventilátor {i+1}", min_run=120.0, min_stop=120.0,
                  spin_up=15.0, rated_kw=7.5)
            for i in range(2)
        ]
        self.t_out = 25.0
        self.t_in = 30.0
        self.basin = 92.0
        self.makeup = 0.0
        self.makeup_total = 0.0
        self.conductivity = 900.0
        self.blowdown = False
        self.pi_fan = PI(kp=22.0, ti=200.0, lo=0.0, hi=100.0)
        self.el_energy = self.reject_energy = 0.0
        self.since_rotate = 0.0
        self.faults = set()

    def set_fault(self, name, on=True):
        (self.faults.add if on else self.faults.discard)(name)
        if name.startswith("fan"):
            self.fans[int(name[3]) - 1].fault = on

    def reset(self):
        """Kvitování poruch ventilátorů věže."""
        for fan in self.fans:
            fan.reset()

    def step(self, dt, hold, amb, reject_kw, flow):
        enable = hold["enable"] > 0.5 and reject_kw > 5.0
        wb = wet_bulb(amb["t_out"], amb["rh"])

        # --- ventilátory: společné otáčky, druhý naskočí až když první nestačí --
        demand = self.pi_fan.step(self.t_out - hold["sp_water_out"], dt) if enable else 0.0
        n_want = 0 if demand < 5 else (1 if demand < 55 else 2)
        speed = clamp(demand * (2.0 if n_want == 1 else 1.0), 0.0, 100.0)

        # ventilátory se po dni provozu vystřídají v pořadí najíždění,
        # aby jeden nenajel dvojnásobek motohodin toho druhého
        self.since_rotate += dt / 3600.0
        healthy = [f for f in self.fans if not f.faulty]
        if self.since_rotate >= 24.0 or n_want == 0:
            healthy.sort(key=lambda f: f.hours)
            if self.since_rotate >= 24.0:
                self.since_rotate = 0.0
        for i, f in enumerate(self.fans):
            on = (not f.faulty) and (healthy.index(f) < n_want if f in healthy else False)
            f.step(dt, on, speed)
        fan_frac = sum(f.speed for f in self.fans) / 200.0

        # --- ochlazení vody ------------------------------------------------------
        # approach roste se zátěží a klesá s otáčkami ventilátorů
        load_frac = clamp(reject_kw / self.reject_nom, 0.0, 1.4)
        approach = APPROACH_MIN + 9.0 * load_frac / (fan_frac + 0.18)
        if not enable:
            approach = 12.0
        target_out = wb + clamp(approach, APPROACH_MIN, 22.0)

        # voda na věž je ohřátá kondenzátory, voda z věže je ochlazená vzduchem
        if flow > 1.0:
            rise = reject_kw / max(water_kw(flow, 1.0), 0.01)
        else:
            rise = 0.0
        self.t_in = lag(self.t_in, self.t_out + rise, dt, 40.0)
        self.t_out = lag(self.t_out, min(target_out, self.t_in), dt, 60.0) + noise(0.05)

        # --- vodní hospodářství ---------------------------------------------------
        evap = EVAP_FRACTION * reject_kw                      # odpar [m³/h]
        drift = 0.0002 * flow
        self.blowdown = self.conductivity > hold["sp_cond_max"]
        bleed = 0.35 if self.blowdown else 0.0
        loss = (evap + drift + bleed) * dt / 3600.0           # [m³ za krok]
        self.basin -= loss / self.basin_m3 * 100.0

        self.makeup = clamp((94.0 - self.basin) * 25.0, 0.0, 100.0)
        add = self.makeup / 100.0 * 1.2 * dt / 3600.0
        self.basin = clamp(self.basin + add / self.basin_m3 * 100.0, 0.0, 100.0)
        self.makeup_total += add

        # odpar odnáší čistou vodu, soli zůstávají -> vodivost roste
        conc = (evap * dt / 3600.0) * 2200.0 / max(self.basin_m3, 0.1)
        dilute = (add + bleed * dt / 3600.0) * self.conductivity / max(self.basin_m3, 0.1)
        self.conductivity = clamp(self.conductivity + conc - dilute, 300.0, 9000.0)

        el = sum(f.power_kw() for f in self.fans)
        self.el_energy += el * dt / 3600.0
        self.reject_energy += reject_kw * dt / 3600.0

        a = 0
        a = set_bit(a, 0, self.basin < 60.0)
        a = set_bit(a, 1, self.fans[0].state == FAULT)
        a = set_bit(a, 2, self.fans[1].state == FAULT)
        a = set_bit(a, 3, self.conductivity > hold["sp_cond_max"] * 1.4)
        # věž nemůže ochladit vodu pod mokrý teploměr, takže se nehlídá
        # nedosažená žádaná hodnota, ale approach při plných otáčkách
        a = set_bit(a, 4, enable and fan_frac > 0.95 and self.t_out - wb > 9.0)
        a = set_bit(a, 5, amb["t_out"] < 2.0 and self.basin > 5.0)

        return {
            "t_water_in": self.t_in, "t_water_out": self.t_out,
            "t_ambient": amb["t_out"], "t_wetbulb": wb,
            "approach": self.t_out - wb,
            "f1_speed": self.fans[0].speed, "f2_speed": self.fans[1].speed,
            "f1_state": self.fans[0].state, "f2_state": self.fans[1].state,
            "flow": flow, "basin_level": self.basin,
            "makeup_valve": self.makeup, "makeup_total": self.makeup_total,
            "conductivity": self.conductivity, "blowdown": 1 if self.blowdown else 0,
            "reject_power": reject_kw, "power": el,
            "el_energy": self.el_energy, "reject_energy": self.reject_energy,
            "state": 2 if any(f.running for f in self.fans) else (0 if not enable else 1),
            "alarms": a,
            "f1_hours": self.fans[0].hours, "f2_hours": self.fans[1].hours,
            "_t_cw_supply": self.t_out,
        }

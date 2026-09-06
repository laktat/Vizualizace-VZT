"""
Model okruhu topné vody v kotelně.

Rozdělovač a sběrač spojují oba kotle se spotřebiči (ohřívače VZT jednotek).
Žádaná teplota na rozdělovači se počítá EKVITERMNĚ — podle venkovní teploty:
čím je venku chladněji, tím teplejší vodu kotelna posílá do systému.

    žádaná = 20 + sklon × (20 − venkovní)^0.8 + posun

Kotle jedou v kaskádě: nabíhá vždy ten s menším počtem motohodin a po
nastaveném intervalu se vedoucí kotel vystřídá. Oběhová čerpadla jsou
zapojená stejně jako u chlazení — provoz / záloha se střídáním.
"""

from .common import (
    DutyStandby, Motor, clamp, lag, noise, set_bit, water_kw,
    RHO_WATER, CP_WATER, FAULT,
)


class HeatingCircuit:
    def __init__(self, dev):
        p = dev.params
        self.dev = dev
        self.flow_nom = p["flow_nom"]
        self.volume = p["volume_m3"]

        # 30 m³/h @ 180 kPa / 0,70 = 2,1 kW na hřídeli -> motor 2,2 kW
        a = Motor("Oběhové čerpadlo 1", min_run=120.0, min_stop=60.0, spin_up=8.0, rated_kw=2.2)
        b = Motor("Oběhové čerpadlo 2", min_run=120.0, min_stop=60.0, spin_up=8.0, rated_kw=2.2)
        self.pumps = DutyStandby(a, b, 24.0)

        self.t_flow = 45.0
        self.t_return = 40.0
        self.p_system = 2.4
        self.p_cold = 1.9             # tlak studeného systému [bar]
        self.makeup = 0.0
        self.n_fire = 0               # kolik kotlů má kaskáda pustit
        self.stage_timer = 0.0
        self.lead = 0                 # index vedoucího kotle
        self.since_change = 0.0
        self.summer = False           # letní odstávka topení
        self.el_energy = self.heat_energy = 0.0
        self.faults = set()

    def set_fault(self, name, on=True):
        (self.faults.add if on else self.faults.discard)(name)
        table = {"hp1": self.pumps.pumps[0], "hp2": self.pumps.pumps[1]}
        if name in table:
            table[name].fault = on

    def reset(self):
        """Kvitování poruch oběhových čerpadel."""
        self.pumps.reset()

    def setpoint(self, hold, t_out):
        """Ekvitermní křivka — žádaná teplota rozdělovače podle venku."""
        d = max(20.0 - t_out, 0.0)
        sp = 20.0 + hold["curve_slope"] * d ** 0.8 + hold["curve_shift"]
        return clamp(sp, hold["sp_min"], hold["sp_max"])

    def update_summer(self, hold, t_out):
        """
        Letní odstávka topení s hysterezí 2 K.

        Nad nastavenou venkovní teplotou se kotelna odstaví celá — kotle
        i oběhová čerpadla. Bez toho by kotle v létě jen krátce cyklovaly
        na ztráty potrubí a zbytečně by přibývaly starty hořáku.
        """
        limit = hold["summer_limit"]
        if self.summer and t_out < limit - 2.0:
            self.summer = False
        elif not self.summer and t_out > limit:
            self.summer = True
        return self.summer

    def stage_boilers(self, dt, hold, n_boilers, boilers):
        """Rozhodne, které kotle mají jet. Vrací seznam povelů True/False."""
        self.since_change += dt / 3600.0
        if self.summer:
            return [False] * n_boilers
        healthy = [i for i, b in enumerate(boilers) if b.available]
        if healthy and self.lead not in healthy:
            self.lead = healthy[0]
        elif len(healthy) > 1 and self.since_change >= hold["changeover_h"]:
            self.lead = healthy[(healthy.index(self.lead) + 1) % len(healthy)]
            self.since_change = 0.0

        # Kaskáda kotlů s hysterezí a prodlevou. Kotel se přidá, až rozdělovač
        # zaostane o 4 K, a ubere, až je o 3 K nad žádanou — a mezi změnami
        # musí uplynout aspoň 4 minuty, jinak by se kotle střídaly zbytečně.
        self.stage_timer += dt
        deficit = self.setpoint_now - self.t_flow
        if self.stage_timer >= 240.0:
            if deficit > 4.0 and self.n_fire < len(healthy):
                # druhý kotel má smysl teprve tehdy, když vedoucí jede naplno
                if self.n_fire == 0 or boilers[self.lead].modulation > 92.0:
                    self.n_fire += 1
                    self.stage_timer = 0.0
            elif deficit < -3.0 and self.n_fire > 0:
                self.n_fire -= 1
                self.stage_timer = 0.0
        self.n_fire = min(self.n_fire, len(healthy))

        order = [self.lead] + [i for i in healthy if i != self.lead]
        fire = set(order[:self.n_fire])
        return [i in fire for i in range(n_boilers)]

    def step(self, dt, hold, amb, boilers, boiler_data, load_kw):
        enable = hold["enable"] > 0.5 and not self.summer
        self.setpoint_now = self.setpoint(hold, amb["t_out"])

        # --- oběhové čerpadlo -------------------------------------------------
        self.pumps.step(dt, enable, 100.0, hold["changeover_h"])
        flow = self.flow_nom * self.pumps.running_speed() / 100.0

        # --- bilance rozdělovače ----------------------------------------------
        heat_in = sum(d["_heat_kw"] for d in boiler_data)
        if flow > 0.5:
            rise = heat_in / max(water_kw(flow, 1.0), 0.01)
            drop = load_kw / max(water_kw(flow, 1.0), 0.01)
        else:
            rise = drop = 0.0
        tau = self.volume * RHO_WATER * CP_WATER / max(water_kw(max(flow, 1.0), 1.0), 0.01)
        self.t_flow = lag(self.t_flow, self.t_return + clamp(rise, 0.0, 45.0),
                          dt, max(tau, 20.0)) + noise(0.04)
        self.t_return = lag(self.t_return, self.t_flow - clamp(drop, 0.0, 40.0), dt, 40.0)

        # --- tlak systému a dopouštění -----------------------------------------
        # Tlak drží expanzní nádoba: se teplotou vody voda expanduje a tlak
        # roste, netěsnostmi pomalu klesá. Když spadne pod 1,6 bar, otevře
        # dopouštěcí ventil a systém se doplní.
        self.p_cold -= 2.0e-6 * dt / 60.0
        self.makeup = clamp((1.6 - self.p_system) * 250.0, 0.0, 100.0)
        self.p_cold = clamp(self.p_cold + self.makeup / 100.0 * 8.0e-5 * dt / 60.0,
                            0.8, 2.4)
        self.p_system = clamp(self.p_cold + (self.t_flow - 30.0) * 0.012, 0.4, 3.5)

        el = sum(p.power_kw() for p in self.pumps.pumps)
        self.el_energy += el * dt / 3600.0
        self.heat_energy += load_kw * dt / 3600.0

        a = 0
        a = set_bit(a, 0, self.pumps.pumps[0].state == FAULT)
        a = set_bit(a, 1, self.pumps.pumps[1].state == FAULT)
        a = set_bit(a, 2, self.p_system < 1.2)
        a = set_bit(a, 3, enable and flow < 0.2 * self.flow_nom)
        a = set_bit(a, 4, enable and self.t_flow < self.setpoint_now - 8.0
                    and all(b["burner"] == 3 for b in boiler_data))
        a = set_bit(a, 5, self.makeup > 1.0)

        out = {
            "t_header_flow": self.t_flow, "t_header_return": self.t_return,
            "t_outdoor": amb["t_out"], "sp_calc": self.setpoint_now,
            "flow": flow, "p_system": self.p_system,
            "p_expansion": self.p_system * 0.92,
            "makeup_valve": self.makeup,
            "heat_power": heat_in, "load_power": load_kw,
            "el_energy": self.el_energy, "heat_energy": self.heat_energy,
            "lead": self.lead + 1, "pump_lead": self.pumps.lead + 1,
            "alarms": a,
            "_flow": flow, "_t_return": self.t_return,
        }
        for prefix, pump in zip(("hp1", "hp2"), self.pumps.pumps):
            frac = pump.speed / 100.0
            out[f"{prefix}_state"] = pump.state
            out[f"{prefix}_speed"] = pump.speed
            out[f"{prefix}_flow"] = flow if pump.speed > 5 else 0.0
            out[f"{prefix}_head"] = 180.0 * frac ** 2
            out[f"{prefix}_power"] = pump.power_kw()
            out[f"{prefix}_hours"] = pump.hours
            out[f"{prefix}_starts"] = pump.starts
        return out

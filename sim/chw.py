"""
Model okruhu chlazené vody.

Zapojení, které se v provozech dělá nejčastěji:

    chillery ── PRIMÁRNÍ okruh (2 čerpadla, konstantní průtok) ──┐
                                                      anuloid    │
    spotřebiče (chladiče VZT) ── SEKUNDÁRNÍ okruh (2 čerpadla ───┘
                                 s frekvenčním měničem, drží
                                 tlakovou diferenci na spotřebiči)

Obě dvojice jsou zapojené jako PROVOZ / ZÁLOHA:
  * běží vždy jen jedno čerpadlo, druhé stojí připravené
  * po nastaveném počtu hodin se automaticky vystřídají, aby se
    opotřebovávala rovnoměrně (registr changeover_h)
  * když vedoucí čerpadlo odejde do poruchy, záloha naskočí okamžitě

Sekundární čerpadlo je otáčkově regulované na tlakovou diferenci: čím víc
chladicích ventilů na jednotkách zavře, tím víc čerpadlo ubere otáčky.
"""

from .common import (
    DutyStandby, Motor, PI, clamp, lag, noise, set_bit, water_kw,
    RHO_WATER, CP_WATER, STANDBY_OR_LOCK, FAULT,
)


def _pair(prefix, label, rated_kw, changeover_h):
    """Dvojice provoz/záloha. rated_kw = příkon na hřídeli při jmenovitém bodu."""
    a = Motor(f"{label} 1", min_run=120.0, min_stop=60.0, spin_up=8.0, rated_kw=rated_kw)
    b = Motor(f"{label} 2", min_run=120.0, min_stop=60.0, spin_up=8.0, rated_kw=rated_kw)
    return DutyStandby(a, b, changeover_h)


class CHWCircuit:
    def __init__(self, dev):
        p = dev.params
        self.dev = dev
        self.prim_nom = p["prim_flow_nom"]
        self.sec_nom = p["sec_flow_nom"]
        self.volume = p["volume_m3"]

        # Příkon čerpadel vychází z jejich pracovního bodu:
        #     P = Q × Δp / účinnost
        # primár  112 m³/h @ 260 kPa / 0,70 = 11,6 kW  -> motor 11 kW
        # sekundár 60 m³/h @ 210 kPa / 0,70 =  5,0 kW  -> motor 5,5 kW
        self.prim = _pair("p", "Primární čerpadlo", 11.0, 24.0)
        self.sec = _pair("s", "Sekundární čerpadlo", 5.5, 24.0)

        self.t_supply = 8.0
        self.t_return = 13.0
        self.dp = 1.2
        self.el_energy = 0.0
        self.pi_dp = PI(kp=45.0, ti=90.0, lo=25.0, hi=100.0, out=70.0)
        self.faults = set()

    def set_fault(self, name, on=True):
        (self.faults.add if on else self.faults.discard)(name)
        table = {"p1": self.prim.pumps[0], "p2": self.prim.pumps[1],
                 "s1": self.sec.pumps[0], "s2": self.sec.pumps[1]}
        if name in table:
            table[name].fault = on

    def step(self, dt, hold, load_kw, valve_demand, chiller_temp, chiller_running,
             demand_active=True):
        """
        valve_demand = 0..1, jak moc mají spotřebiče otevřené chladicí ventily.
        Je to ono místo, kde se hydraulika potkává s regulací jednotek: zavřený
        ventil na VZT znamená menší průtok okruhem a vyšší tlakovou diferenci.
        """
        # čerpadla běží jen tehdy, když je o chlad zájem — se zavřenými ventily
        # by se voda okruhem hnala zbytečně a čerpadlo by ji jen ohřívalo
        enable = hold["enable"] > 0.5 and demand_active
        co = hold["changeover_h"]

        # --- primární čerpadlo: konstantní otáčky, běží když jede chlazení -----
        self.prim.step(dt, enable, 100.0, co)
        flow_prim = self.prim_nom * self.prim.running_speed() / 100.0

        # --- sekundární čerpadlo: otáčky drží tlakovou diferenci --------------
        speed_cmd = self.pi_dp.step(hold["sp_dp"] - self.dp, dt) if enable else 0.0
        self.sec.step(dt, enable, speed_cmd, co)
        sec_speed = self.sec.running_speed()

        # kolik vody projde: dané otevřením ventilů a otáčkami čerpadla.
        # I při zavřených ventilech teče přes obtok minimum, aby čerpadlo
        # nepracovalo do uzavřené sítě.
        valve = clamp(valve_demand, 0.0, 1.0)
        flow_sec = self.sec_nom * valve * (sec_speed / 100.0)
        if sec_speed > 5.0:
            flow_sec = max(flow_sec, 0.12 * self.sec_nom)

        # zavřené ventily zvednou tlak -> čerpadlo přes PI ubere otáčky
        dp_target = (2.6 * (sec_speed / 100.0) ** 2
                     - 1.4 * (flow_sec / self.sec_nom) ** 2)
        self.dp = lag(self.dp, clamp(dp_target, 0.05, 3.5), dt, 12.0)
        p_supply = 4.2 + self.dp / 2.0 + noise(0.01)
        p_return = p_supply - self.dp

        # --- teploty ------------------------------------------------------------
        # zpátečka se ohřeje o teplo odebrané z hal
        rise = load_kw / max(water_kw(flow_sec, 1.0), 0.01) if flow_sec > 1.0 else 0.0
        self.t_return = lag(self.t_return, self.t_supply + clamp(rise, 0.0, 12.0), dt, 45.0)

        # ANULOID (termohydraulický rozdělovač) odděluje primár od sekundáru.
        # Když primární čerpadlo žene víc vody, než spotřebiče odeberou, přebytek
        # studené vody se přelije rovnou do zpátečky a chillery dostanou na vstup
        # chladnější směs. Je to ta pověstná "nízká delta T" v chladicích okruzích.
        if flow_prim > flow_sec + 0.5:
            bypass = flow_prim - flow_sec
            t_chiller_in = (flow_sec * self.t_return + bypass * self.t_supply) / flow_prim
        else:
            t_chiller_in = self.t_return

        # přívod je to, co dodají chillery; při jejich odstávce se ohřívá
        tau = self.volume * RHO_WATER * CP_WATER / max(water_kw(max(flow_prim, 1.0), 1.0), 0.01)
        target = chiller_temp if chiller_running else self.t_return
        self.t_supply = lag(self.t_supply, target, dt, max(tau, 15.0)) + noise(0.03)

        el = sum(p.power_kw() for p in self.prim.pumps + self.sec.pumps)
        self.el_energy += el * dt / 3600.0

        a = 0
        for bit, pump in enumerate(self.prim.pumps + self.sec.pumps):
            a = set_bit(a, bit, pump.state == FAULT)
        a = set_bit(a, 4, enable and flow_sec < 0.10 * self.sec_nom and load_kw > 20)
        a = set_bit(a, 5, p_return < 1.5)
        a = set_bit(a, 6, enable and self.t_supply > hold["sp_supply"] + 4.0)

        out = {
            "t_supply": self.t_supply, "t_return": self.t_return,
            "flow_prim": flow_prim, "flow_sec": flow_sec,
            "p_supply": p_supply, "p_return": p_return, "dp": self.dp,
            "load_power": load_kw, "el_energy": self.el_energy,
            "prim_lead": self.prim.lead + 1, "sec_lead": self.sec.lead + 1,
            "alarms": a,
            "_flow_prim": flow_prim, "_t_chiller_in": t_chiller_in,
        }
        heads = {"p1": 260.0, "p2": 260.0, "s1": 210.0, "s2": 210.0}
        flows = {"p1": flow_prim, "p2": flow_prim, "s1": flow_sec, "s2": flow_sec}
        for prefix, pump in zip(("p1", "p2", "s1", "s2"),
                                self.prim.pumps + self.sec.pumps):
            frac = pump.speed / 100.0
            out[f"{prefix}_state"] = pump.state
            out[f"{prefix}_speed"] = pump.speed
            out[f"{prefix}_flow"] = flows[prefix] if pump.speed > 5 else 0.0
            out[f"{prefix}_head"] = heads[prefix] * frac ** 2
            out[f"{prefix}_power"] = pump.power_kw()
            out[f"{prefix}_hours"] = pump.hours
            out[f"{prefix}_starts"] = pump.starts
        return out

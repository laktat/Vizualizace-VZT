"""
Dispečer celého závodu — drží dohromady jednotlivé technologie.

Zařízení nejsou nezávislé ostrovy, ale spojená soustava. Model to počítá
v tomhle pořadí, přesně jak teplo v závodě teče:

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

Prakticky to znamená, že když v lakovně otevřeš chladicí ventil, za chvíli
se ohřeje zpátečka chlazené vody, naskočí další kompresor a rozběhne se
druhý ventilátor na věži. Přesně tak se to chová i v provozu.
"""

import math
import random

from .common import clamp, lag, noise, wet_bulb, Sequencer
from .ahu import AHU
from .chiller import Chiller
from .tower import Tower
from .chw import CHWCircuit
from .boiler import Boiler
from .hw import HeatingCircuit

MAX_SUBSTEP = 5.0        # nejdelší dílčí krok simulace [s]


class Ambient:
    """
    Počasí. Den trvá DAY_SECONDS simulovaného času (výchozí 24 h), takže při
    zrychlení 60× uvidíš celý denní cyklus za 24 minut sledování.

    K dennímu průběhu se přičítá pomalá změna počasí (fronty, ochlazení),
    aby technologie nejela pořád ve stejném pracovním bodě.
    """

    DAY_SECONDS = 24 * 3600.0

    def __init__(self, season="jaro"):
        base = {"zima": -2.0, "jaro": 12.0, "leto": 26.0, "podzim": 9.0}
        self.base = base.get(season, 12.0)
        self.amplitude = 5.0 if season == "zima" else 8.0
        self.t = 6 * 3600.0        # start simulace v 6 ráno
        self.drift = 0.0
        self.t_out = self.base

    def step(self, dt):
        self.t += dt
        hour = (self.t % self.DAY_SECONDS) / 3600.0
        # denní chod: nejchladněji kolem 5:00, nejtepleji kolem 15:00
        daily = -math.cos((hour - 5.0) / 24.0 * 2 * math.pi)
        self.drift = lag(self.drift, random.gauss(0.0, 3.0), dt, 6 * 3600.0)
        target = self.base + self.amplitude * daily + self.drift
        self.t_out = lag(self.t_out, target, dt, 300.0) + noise(0.05)

        # vlhkost: přes den s teplotou klesá
        rh = clamp(72.0 - (self.t_out - self.base) * 2.2 + noise(1.0), 20.0, 96.0)
        # provozní faktor: v noci a o víkendu jede závod na útlum
        day = int(self.t // self.DAY_SECONDS)
        weekend = day % 7 >= 5
        shift = 1.0 if 6.0 <= hour < 22.0 else 0.25
        return {
            "t_out": self.t_out, "rh": rh, "hour": hour,
            "wetbulb": wet_bulb(self.t_out, rh),
            "day_factor": (0.3 if weekend else 1.0) * shift,
            "weekend": weekend,
        }


class ChillerPlant:
    """
    Nadřazená regulace strojovny chlazení — kolik chillerů má jet.

    Pořadí najíždění se srovná podle motohodin, aby se provoz mezi stroje
    rozložil, ale NE v každém kroku: přerovnává se jen při odstavené
    strojovně nebo po nastaveném intervalu střídání. Kdyby se pořadí měnilo
    pořád, stroje by si mezi sebou přehazovaly průtok a okruh by se místo
    chlazení ohříval.
    """

    def __init__(self, chillers, rotate_h=24.0):
        self.chillers = chillers
        self.order = list(chillers)
        self.rotate_h = rotate_h
        self.n_run = 0
        self.timer = 0.0
        self.since_rotate = 0.0

    def step(self, dt, load_kw, t_supply, sp, demand_active):
        self.timer += dt
        self.since_rotate += dt / 3600.0
        avg_kw = sum(c.capacity_kw for c in self.chillers) / len(self.chillers)

        if not demand_active:
            # nikdo nechce chlad (zima, noc) — strojovna stojí celá.
            # Bez toho by chillery donekonečna cyklovaly na vlastní ztráty.
            want = 0
        else:
            want = math.ceil(load_kw / (avg_kw * 0.75)) if load_kw > 15.0 else 0
            if t_supply > sp + 2.0:        # okruh se nedaří udržet -> přidej stroj
                want += 1
        want = int(clamp(want, 0, len(self.chillers)))

        if self.n_run == 0 or self.since_rotate >= self.rotate_h:
            self.order = sorted(self.chillers,
                                key=lambda c: sum(m.hours for m in c.comps))
            self.since_rotate = 0.0

        # najíždí a odstavuje se vždy po jednom stroji, s prodlevou
        if self.timer > 180.0 and want != self.n_run:
            self.n_run += 1 if want > self.n_run else -1
            self.timer = 0.0

        return {id(c): (i < self.n_run) for i, c in enumerate(self.order)}


class Factory:
    def __init__(self, devices, season="jaro"):
        self.ambient = Ambient(season)
        self.devices = devices
        self.models = {}
        for d in devices:
            cls = {"ahu": AHU, "chiller": Chiller, "tower": Tower,
                   "chw": CHWCircuit, "boiler": Boiler, "hw": HeatingCircuit}[d.type]
            self.models[d.id] = cls(d)

        self.ahus = [d.id for d in devices if d.type == "ahu"]
        self.chillers = [d.id for d in devices if d.type == "chiller"]
        self.boilers = [d.id for d in devices if d.type == "boiler"]
        self.plant_ctl = ChillerPlant([self.models[i] for i in self.chillers])
        self.last = {}

    def set_fault(self, device_id, name, on=True):
        self.models[device_id].set_fault(name, on)

    def step(self, dt, holdings):
        """
        Posune závod o dt sekund provozu.

        Regulátory v poli vzorkují každou sekundu, ne každou minutu. Kdyby se
        při zrychleném čase počítal jeden krok o délce minuty, smyčky by se
        rozkmitaly a jednotka by topila a chladila zároveň. Proto se dlouhý
        krok rozdělí na dílčí kroky nejvýš MAX_SUBSTEP dlouhé.
        """
        n = max(1, int(math.ceil(dt / MAX_SUBSTEP)))
        sub = dt / n
        for _ in range(n):
            out = self._step_once(sub, holdings)
        return out

    def _step_once(self, dt, holdings):
        """Jeden dílčí krok — tady se počítá fyzika a regulace."""
        amb = self.ambient.step(dt)
        out = {}

        chw_model = self.models["chw"]
        hw_model = self.models["kotelna"]
        tower = self.models["vez"]
        t_chw = chw_model.t_supply
        t_hw = hw_model.t_flow

        # --- 1) spotřebiče: vzduchotechnika -----------------------------------
        chw_load = hw_load = 0.0
        valve_open = valve_total = 0.0
        for aid in self.ahus:
            m = self.models[aid]
            d = m.step(dt, holdings[aid], amb, t_hw, t_chw)
            out[aid] = d
            chw_load += d["_chw_kw"]
            hw_load += d["_hw_kw"]
            # otevření chladicích ventilů, vážené velikostí chladiče —
            # z toho vychází, kolik vody potřebuje sekundární okruh
            valve_open += d["cool_cmd"] / 100.0 * m.cooler_kw
            valve_total += m.cooler_kw
        valve_demand = valve_open / valve_total if valve_total else 0.0

        # --- 2) okruh chlazené vody --------------------------------------------
        # do okruhu se mísí voda jen z chillerů, kterými opravdu teče —
        # odstavený stroj je uzavřený a jeho teplota se do směsi nepočítá
        prev = self.last
        num = den = 0.0
        for cid in self.chillers:
            d = prev.get(cid)
            if d and d["flow_chw"] > 1.0:
                num += d["t_chw_out"] * d["flow_chw"]
                den += d["flow_chw"]
        running = den > 0.0
        chw_temp = num / den if running else chw_model.t_return
        # chlazení se pouští, teprve když jsou ventily na jednotkách otevřené
        cooling_demand = valve_demand > 0.03 or chw_load > 10.0
        out["chw"] = chw_model.step(dt, holdings["chw"], chw_load, valve_demand,
                                    chw_temp, running, cooling_demand)

        # --- 3) strojovna chlazení ---------------------------------------------
        enables = self.plant_ctl.step(dt, chw_load, chw_model.t_supply,
                                      holdings["chw"]["sp_supply"], cooling_demand)
        n_on = max(sum(enables.values()), 1)
        flow_each = out["chw"]["flow_prim"] / n_on
        t_cw = tower.t_out
        reject = 0.0
        for cid in self.chillers:
            m = self.models[cid]
            hold = dict(holdings[cid])
            # operátor může chiller zakázat; nadřazená regulace ho jen nepustí navíc
            hold["enable"] = hold["enable"] if enables[id(m)] else 0.0
            d = m.step(dt, hold, out["chw"]["_t_chiller_in"], t_cw,
                       flow_each if enables[id(m)] else 0.0)
            out[cid] = d
            reject += d["_reject_kw"]

        # --- 4) chladicí věž ----------------------------------------------------
        cw_flow = tower.flow_nom * clamp(n_on / len(self.chillers), 0.34, 1.0)
        out["vez"] = tower.step(dt, holdings["vez"], amb, reject, cw_flow)

        # --- 5) kotelna ---------------------------------------------------------
        boilers = [self.models[b] for b in self.boilers]
        hw_model.setpoint_now = hw_model.setpoint(holdings["kotelna"], amb["t_out"])
        hw_model.update_summer(holdings["kotelna"], amb["t_out"])
        cmds = hw_model.stage_boilers(dt, holdings["kotelna"], len(boilers), boilers)
        n_fire = max(sum(cmds), 1)
        b_data = []
        for bid, cmd in zip(self.boilers, cmds):
            # V kaskádě žádanou teplotu kotli určuje nadřazená regulace kotelny
            # (ekvitermně, plus nadvýšení na ztráty rozvodů). Holding registr
            # kotle přitom platí jako HORNÍ mez, kterou operátor nastavuje.
            hold = dict(holdings[bid])
            hold["sp_flow"] = clamp(hw_model.setpoint_now + 4.0, 40.0, hold["sp_flow"])
            d = self.models[bid].step(dt, hold, cmd, hw_model.t_return,
                                      hw_model.flow_nom / n_fire if cmd else 0.0)
            out[bid] = d
            b_data.append(d)
        out["kotelna"] = hw_model.step(dt, holdings["kotelna"], amb, boilers,
                                       b_data, hw_load)

        out["_ambient"] = amb
        self.last = out
        return out

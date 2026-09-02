"""
Model chladicí jednotky (chiller) se dvěma kompresory a vodou chlazeným
kondenzátorem napojeným na chladicí věž.

Chladicí okruh:
    výparník (chlazená voda) -> kompresor 1+2 -> kondenzátor (voda z věže)
    -> expanzní ventil -> zpět do výparníku

Co model počítá věrně a co ve vizualizaci uvidíš:
  * vypařovací a kondenzační teplotu a z nich TLAKY CHLADIVA (sání/výtlak)
  * přehřátí sání, které drží elektronický expanzní ventil
  * postupné najíždění kompresorů podle odchylky teploty chlazené vody,
    s minimální dobou chodu a pauzy (kompresor se nesmí často spínat)
  * motohodiny a počty startů zvlášť pro každý kompresor; nabíhá vždy ten
    s menším počtem hodin, takže se opotřebení rozkládá
  * pokles účinnosti (COP) při vysoké kondenzační teplotě — když věž
    nestíhá chladit, chiller žere víc proudu za stejný chlad
"""

from .common import (
    PI, Motor, Sequencer, clamp, lag, noise, sat_pressure, set_bit, water_kw,
    RUN, FAULT,
)

ST_STOP, ST_READY, ST_COOL, ST_UNLOAD, ST_FAULT = 0, 1, 2, 3, 4

EVAP_APPROACH = 4.5      # o kolik je chladivo studenější než voda z výparníku [K]
COND_APPROACH = 6.0      # o kolik je chladivo teplejší než voda do kondenzátoru [K]
COND_MIN = 22.0          # nejnižší kondenzační teplota, kterou regulace drží [°C]
SUPERHEAT_SP = 5.0       # žádané přehřátí sání [K]


class Chiller:
    def __init__(self, dev):
        p = dev.params
        self.dev = dev
        self.capacity_kw = p["capacity_kw"]
        self.flow_nom = p["flow_nom"]
        self.comp_kw = p["comp_kw"]

        # kompresory: dlouhá minimální pauza, aby se motor stihl ochladit
        self.comps = [
            Motor(f"Kompresor {i+1}", hours=p.get(f"c{i+1}_hours", 0.0),
                  min_run=300.0, min_stop=360.0, spin_up=20.0,
                  rated_kw=self.comp_kw)
            for i in range(2)
        ]
        self.t_chw_out = 12.0
        self.t_cond = 35.0
        self.eev = 40.0
        self.superheat = SUPERHEAT_SP
        self.pi_cap = PI(kp=25.0, ti=240.0, lo=0.0, hi=100.0)
        self.stage_timer = 0.0
        self.state = ST_STOP
        self.faults = set()

    def set_fault(self, name, on=True):
        (self.faults.add if on else self.faults.discard)(name)
        if name.startswith("comp"):
            self.comps[int(name[4]) - 1].fault = on

    @property
    def running_comps(self):
        return [c for c in self.comps if c.running]

    def step(self, dt, hold, t_chw_in, t_cw_in, flow_chw):
        enable = hold["enable"] > 0.5
        sp = hold["sp_chw_out"]

        # --- kolik výkonu je potřeba ------------------------------------------
        demand = self.pi_cap.step(self.t_chw_out - sp, dt) if enable else 0.0
        demand = min(demand, hold["cap_limit"])

        # --- najíždění a odstavování kompresorů --------------------------------
        # jeden kompresor pokryje polovinu výkonu; druhý naskočí, až první
        # nestačí, a to ne dřív než po prodlevě, aby stroj necykloval
        self.stage_timer += dt
        want = 0 if not enable else (1 if demand > 8.0 else 0)
        if demand > 55.0:
            want = 2
        n_run = len(self.running_comps)

        # každý kompresor drží svůj povel; sekvencer v jednom kroku přidá
        # nebo ubere nejvýš jeden stroj, aby soustava nekmitala
        cmds = [c.running for c in self.comps]
        if self.stage_timer > 60.0 and want != n_run:
            m = (Sequencer.pick_start(self.comps) if want > n_run
                 else Sequencer.pick_stop(self.comps))
            if m:
                cmds[self.comps.index(m)] = want > n_run
                self.stage_timer = 0.0

        for c, on in zip(self.comps, cmds):
            c.step(dt, on, 100.0)

        n_run = len(self.running_comps)
        speed = sum(c.speed for c in self.comps) / 200.0        # 0..1
        # výkon jednotky: první kompresor moduluje, druhý jede naplno
        capacity = clamp(speed * 100.0 * (0.5 + 0.5 * min(demand / 60.0, 1.0)), 0.0, 100.0)

        # --- chladicí okruh ----------------------------------------------------
        # kondenzační teplota se řídí vodou z věže, vypařovací vodou z výparníku
        t_evap_target = self.t_chw_out - EVAP_APPROACH if capacity > 1 else t_chw_in - 1
        # regulace kondenzačního tlaku nedovolí kondenzaci spadnout moc nízko —
        # jinak by přes expanzní ventil neprošlo dost chladiva
        t_cond_target = max(t_cw_in + COND_APPROACH + 8.0 * (capacity / 100.0), COND_MIN)
        self.t_evap = lag(getattr(self, "t_evap", 5.0), t_evap_target, dt, 25.0)
        # po zastavení se tlaky v okruhu vyrovnají — kondenzace klesne k vypařovací
        self.t_cond = lag(self.t_cond, t_cond_target if capacity > 1 else self.t_evap,
                          dt, 40.0)

        p_suction = sat_pressure(self.t_evap) + noise(0.02)
        p_discharge = sat_pressure(self.t_cond) + noise(0.03)

        # expanzní ventil drží přehřátí na žádané hodnotě
        if capacity > 1:
            err = SUPERHEAT_SP - self.superheat
            self.eev = clamp(self.eev - err * 2.0 * dt / 10.0, 8.0, 100.0)
            self.superheat = lag(self.superheat,
                                 SUPERHEAT_SP + (45.0 - self.eev) * 0.05 + noise(0.3),
                                 dt, 15.0)
        else:
            self.eev = lag(self.eev, 0.0, dt, 10.0)
            self.superheat = lag(self.superheat, 0.0, dt, 30.0)
        subcool = 4.0 + 2.0 * (capacity / 100.0) + noise(0.1) if capacity > 1 else 0.0

        # --- výkon a spotřeba ---------------------------------------------------
        # účinnost padá s rostoucím rozdílem kondenzační a vypařovací teploty
        lift = max(self.t_cond - self.t_evap, 5.0)
        derate = clamp(1.15 - lift / 120.0, 0.55, 1.05)
        cool_power = self.capacity_kw * capacity / 100.0 * derate
        power = (self.comp_kw * n_run * (0.25 + 0.75 * capacity / 100.0)
                 * clamp(lift / 38.0, 0.6, 1.6)) if n_run else 0.0
        cop = cool_power / power if power > 1.0 else 0.0

        # --- teplota vody z výparníku -------------------------------------------
        if flow_chw > 1.0:
            dt_water = cool_power / max(water_kw(flow_chw, 1.0), 0.01)
            self.t_chw_out = lag(self.t_chw_out, t_chw_in - dt_water, dt, 30.0)
        else:
            self.t_chw_out = lag(self.t_chw_out, t_chw_in, dt, 60.0)
        t_cw_out = t_cw_in + (cool_power + power) / max(water_kw(self.flow_nom * 1.25, 1.0), 0.01)

        # --- stav a alarmy -------------------------------------------------------
        if any(c.state == FAULT for c in self.comps):
            self.state = ST_FAULT
        elif not enable:
            self.state = ST_STOP
        elif n_run == 0:
            self.state = ST_READY
        else:
            self.state = ST_COOL

        a = 0
        a = set_bit(a, 0, p_discharge > 30.0)
        a = set_bit(a, 1, n_run > 0 and p_suction < 5.0)
        a = set_bit(a, 2, n_run > 0 and flow_chw < 0.4 * self.flow_nom)
        a = set_bit(a, 3, n_run > 0 and self.superheat < 2.0)
        a = set_bit(a, 4, self.comps[0].state == FAULT)
        a = set_bit(a, 5, self.comps[1].state == FAULT)
        a = set_bit(a, 6, self.state == ST_COOL and self.t_chw_out > sp + 3.0)

        return {
            "t_chw_in": t_chw_in, "t_chw_out": self.t_chw_out,
            "t_cw_in": t_cw_in, "t_cw_out": t_cw_out,
            "p_suction": p_suction, "p_discharge": p_discharge,
            "t_evap": self.t_evap, "t_cond": self.t_cond,
            "superheat": self.superheat, "subcool": subcool,
            "eev": self.eev, "capacity": capacity,
            "flow_chw": flow_chw, "power": power,
            "cool_power": cool_power, "cop": cop,
            "current": power / 0.4 / 0.9,
            "c1_state": self.comps[0].state, "c2_state": self.comps[1].state,
            "state": self.state, "alarms": a,
            "c1_hours": self.comps[0].hours, "c2_hours": self.comps[1].hours,
            "c1_starts": self.comps[0].starts, "c2_starts": self.comps[1].starts,
            # -- vazba na věž --
            "_reject_kw": cool_power + power,
            "_cool_kw": cool_power,
        }

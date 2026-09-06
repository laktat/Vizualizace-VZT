"""
Model vzduchotechnické jednotky.

Cesta vzduchu:
    venku -> filtr -> rekuperátor -> vodní ohřívač -> vodní chladič
          -> přívodní ventilátor -> hala
    hala -> filtr -> odtahový ventilátor -> rekuperátor -> odpad

Regulace je kaskádní, jak to dělá každý slušný regulátor v poli:
    nadřazená smyčka drží teplotu v hale (čidlo v odtahu) a z odchylky
    počítá ŽÁDANOU TEPLOTU PŘÍVODU, omezenou mezemi sp_supply_min/max
    podřízená smyčka drží teplotu přívodu a rozděluje povel do sekvence
    rekuperace -> ohřev -> chlazení (vždy jen jedno z toho, s pásmem necitlivosti)

Jednotka odebírá teplo z okruhu topné vody a chlad z okruhu chlazené vody —
kolik, to hlásí do plant.py, které z toho skládá zátěž kotelny a chillerů.
"""

from .common import (
    CP_AIR, RHO_AIR, PI, Motor, air_kw, clamp, lag, noise, set_bit, wet_bulb,
    STOP, START, RUN, FAULT,
)

ST_STOP, ST_RAMP, ST_RUN, ST_COOLDOWN, ST_FAULT = 0, 1, 2, 3, 4


class AHU:
    def __init__(self, dev):
        p = dev.params
        self.dev = dev
        self.flow_nom = p["flow_nom"]
        self.heater_kw = p["heater_kw"]
        self.cooler_kw = p["cooler_kw"]
        self.recup_eff = p["recup_eff"]
        self.filter_wear = p["filter_wear"]
        self.internal_gain = p["internal_gain"]

        # tepelná kapacita prostoru: vzduch + zabudovaná hmota (stěny, stroje)
        self.room_capacity = p["room_volume"] * RHO_AIR * CP_AIR * 12.0   # [kJ/K]
        # ztráta obálkou: cca 0,12 W/K na m³ obestavěného prostoru
        self.envelope_ua = p["room_volume"] * 0.00012                     # [kW/K]

        # Příkon ventilátorů je nastavený tak, aby měrný příkon jednotky
        # (SFP) vyšel při návrhovém průtoku na 2,0 kW/(m³/s) — tolik má
        # dnešní jednotka se dvěma ventilátory a rekuperací mít. Při nižších
        # otáčkách klesá s třetí mocninou, takže v běžném provozu je nižší.
        self.fan_sup = Motor("Přívodní ventilátor", min_run=30, min_stop=30,
                             spin_up=12.0, rated_kw=p["flow_nom"] * 0.00031)
        self.fan_ext = Motor("Odtahový ventilátor", min_run=30, min_stop=30,
                             spin_up=12.0, rated_kw=p["flow_nom"] * 0.00025)

        self.room = 20.0
        self.t_supply = 20.0
        self.t_after_heater = 20.0
        self.dp_sup = 45.0
        self.dp_ext = 40.0
        self.run_hours = float(p.get("run_hours", 0.0))
        # stavy podružného měření — narůstají, nikdy se nenulují
        self.el_energy = self.heat_energy = self.cool_energy = 0.0
        self.recup_energy = self.waste_energy = 0.0
        self.state = ST_STOP

        self.pi_room = PI(kp=1.6, ti=600.0, lo=-12.0, hi=14.0)   # výstup = korekce přívodu [K]
        self.pi_supply = PI(kp=12.0, ti=180.0, lo=-100.0, hi=100.0)
        self.heat_cmd = self.cool_cmd = 0.0
        self.recup_cmd = 100.0
        self.damper = 0.0
        self.faults = set()

    # -- poruchy pro test vyhodnocení -----------------------------------------
    def set_fault(self, name, on=True):
        (self.faults.add if on else self.faults.discard)(name)
        if name == "fan-fault":
            self.fan_sup.fault = on

    def reset(self):
        """Kvitování poruch jednotky — ventilátory smí zkusit znovu naběhnout."""
        self.fan_sup.reset()
        self.fan_ext.reset()

    def step(self, dt, hold, amb, t_hw, t_chw):
        mode = int(round(hold["mode"]))
        running = mode > 0

        # --- ventilátory ------------------------------------------------------
        sp_fan = hold["sp_fan"]
        self.fan_sup.step(dt, running, sp_fan)
        self.fan_ext.step(dt, running, sp_fan * 0.95)
        fan = self.fan_sup.speed
        flow = self.flow_nom * (fan / 100.0)
        # zanesený filtr přiškrtí průtok
        flow *= clamp(1.0 - (self.dp_sup - 45.0) / 1200.0, 0.55, 1.0)

        # --- rekuperace -------------------------------------------------------
        # v létě, když je venku chladněji než v hale, se rekuperátor obchází
        # (noční předchlazení zdarma); jinak jede naplno
        want_cooling = self.room > hold["sp_room"] + 0.3
        free_cooling = want_cooling and amb["t_out"] < self.room - 1.5
        self.recup_cmd = lag(self.recup_cmd, 0.0 if free_cooling else 100.0, dt, 60.0)
        eff = self.recup_eff * (self.recup_cmd / 100.0) * (0.9 if fan > 90 else 1.0)
        t_out = amb["t_out"]
        t_after_recup = t_out + eff * (self.room - t_out) if fan > 5 else t_out
        self.damper = lag(self.damper, 100.0 if running else 0.0, dt, 20.0)

        # --- kaskáda: hala -> žádaná teplota přívodu ---------------------------
        if running:
            corr = self.pi_room.step(hold["sp_room"] - self.room, dt)
            sp_supply = clamp(hold["sp_room"] + corr,
                              hold["sp_supply_min"], hold["sp_supply_max"])
        else:
            sp_supply = self.room

        # --- podřízená smyčka: sekvence ohřev / necitlivost / chlazení ---------
        # regulátor pracuje s tím, co mu hlásí ČIDLO přívodu. Když je čidlo
        # vadné, přepne se na náhradní veličinu (teplotu v hale) a hlásí
        # poruchu — tak se to dělá, aby porucha čidla nerozhodila regulaci.
        t_supply_sensor = -120.0 if "sensor-fail" in self.faults else self.t_supply
        sensor_bad = not (-50.0 < t_supply_sensor < 120.0)
        measured = self.room if sensor_bad else t_supply_sensor
        seq = self.pi_supply.step(sp_supply - measured, dt) if running else -100.0
        heat_cmd = clamp(seq - 5.0, 0.0, 100.0)          # nad +5 % topí
        cool_cmd = clamp(-seq - 5.0, 0.0, 100.0)         # pod -5 % chladí

        # --- ohřívač ----------------------------------------------------------
        # kolik ohřívač zvládne, omezuje teplota topné vody a průtok vzduchu
        max_heat_dt = 0.0
        if flow > 100:
            max_heat_dt = min(self.heater_kw / max(air_kw(flow, 1.0), 0.01),
                              max(t_hw - t_after_recup - 5.0, 0.0))
        # porucha: pohon ventilu se zasekne v poloze a topí i při povelu 0 %
        actual_heat = 32.0 if "stuck-valve" in self.faults else heat_cmd
        self.t_after_heater = t_after_recup + actual_heat / 100.0 * max_heat_dt

        # --- chladič ----------------------------------------------------------
        max_cool_dt = 0.0
        if flow > 100:
            max_cool_dt = min(self.cooler_kw / max(air_kw(flow, 1.0), 0.01),
                              max(self.t_after_heater - t_chw - 3.0, 0.0))
        t_supply = self.t_after_heater - cool_cmd / 100.0 * max_cool_dt
        self.t_supply = lag(self.t_supply, t_supply, dt, 20.0) + noise(0.05)
        self.heat_cmd, self.cool_cmd = heat_cmd, cool_cmd

        # výměník umí přenášet teplo jen jedním směrem — ohřívač nechladí
        hw_power = max(air_kw(flow, self.t_after_heater - t_after_recup), 0.0)
        chw_power = max(air_kw(flow, self.t_after_heater - self.t_supply), 0.0)

        # --- teplota v hale ----------------------------------------------------
        q_air = air_kw(flow, self.t_supply - self.room)      # přívodní vzduch
        q_gain = self.internal_gain * (0.35 + 0.65 * amb["day_factor"])
        q_loss = self.envelope_ua * (self.room - t_out)
        self.room += (q_air + q_gain - q_loss) / self.room_capacity * dt
        t_extract = self.room + noise(0.06)
        t_exhaust = t_extract - eff * (t_extract - t_out)

        # --- filtry a provozní hodiny -----------------------------------------
        if self.fan_sup.running:
            load = (fan / 78.0) ** 2
            self.dp_sup += self.filter_wear * load * dt / 3600.0
            self.dp_ext += self.filter_wear * 0.6 * load * dt / 3600.0
            self.run_hours += dt / 3600.0

        # --- počítadla energie -------------------------------------------------
        el = self.fan_sup.power_kw() + self.fan_ext.power_kw()
        h = dt / 3600.0
        self.el_energy += el * h
        self.heat_energy += hw_power * h
        self.cool_energy += chw_power * h
        # Kolik práce ušetřil rekuperátor: o co posunul teplotu sání proti
        # venkovní. V zimě předehřeje (ušetří ohřívači), v létě předchladí
        # (ušetří chladiči) — obojí je energie, kterou nemusel dodat výměník.
        self.recup_energy += abs(air_kw(flow, t_after_recup - t_out)) * h
        # Zmařené teplo: ohřívač topí, i když regulace poslala zavřít. Chladič
        # to pak musí odebrat, takže se ta samá energie platí dvakrát.
        if heat_cmd < 1.0:
            self.waste_energy += hw_power * h

        # --- stav jednotky -----------------------------------------------------
        if self.fan_sup.state == FAULT:
            self.state = ST_FAULT
        elif not running:
            self.state = ST_COOLDOWN if self.fan_sup.speed > 2 else ST_STOP
        else:
            self.state = ST_RUN if self.fan_sup.state == RUN else ST_RAMP

        # --- alarmy ------------------------------------------------------------
        t_supply_meas = t_supply_sensor
        a = 0
        a = set_bit(a, 0, self.dp_sup > hold["dp_limit"])
        a = set_bit(a, 1, self.dp_ext > hold["dp_limit"])
        a = set_bit(a, 2, sensor_bad)
        a = set_bit(a, 3, heat_cmd < 1.0 and hw_power > 0.05 * self.heater_kw)
        a = set_bit(a, 4, self.fan_sup.state == FAULT or self.fan_ext.state == FAULT)
        a = set_bit(a, 5, running and self.t_after_heater < 5.0 and t_out < 3.0)
        a = set_bit(a, 6, self.state == ST_RUN and abs(self.room - hold["sp_room"]) > 3.0)

        rh = clamp(45.0 - (self.room - 22.0) * 1.5, 20.0, 75.0)
        return {
            "t_outdoor": t_out,
            "t_after_recup": t_after_recup,
            "t_after_heater": self.t_after_heater,
            "t_supply": t_supply_meas,
            "t_extract": t_extract,
            "t_exhaust": t_exhaust,
            "rh_extract": rh,
            "heat_cmd": heat_cmd,
            "cool_cmd": cool_cmd,
            "recup_cmd": self.recup_cmd,
            "damper": self.damper,
            "fan_supply": self.fan_sup.speed,
            "fan_extract": self.fan_ext.speed,
            "flow_supply": flow,
            "filter_dp_sup": self.dp_sup,
            "filter_dp_ext": self.dp_ext,
            "current": (self.fan_sup.power_kw() + self.fan_ext.power_kw()) / 0.4,
            "power": self.fan_sup.power_kw() + self.fan_ext.power_kw(),
            "chw_power": chw_power,
            "hw_power": hw_power,
            "state": self.state,
            "alarms": a,
            "run_hours": self.run_hours,
            "el_energy": self.el_energy,
            "heat_energy": self.heat_energy,
            "cool_energy": self.cool_energy,
            "recup_energy": self.recup_energy,
            "waste_energy": self.waste_energy,
            # -- pro vazbu na ostatní technologie (nejde do registrů) --
            "_chw_kw": chw_power,
            "_hw_kw": hw_power,
        }

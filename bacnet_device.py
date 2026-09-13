"""
Simulovaná VZT3 jako BACnet/IP zařízení.

Tohle je protějšek Modbus serverů v simulator.py — strana ZAŘÍZENÍ. Běží
ve stejné asyncio smyčce jako zbytek závodu a fyziku si nepočítá: dostává
hotové hodnoty ze sim/factory.py a jen je vystavuje jako BACnet objekty.

Dvě věci, na které se na loopbacku naráží:

  * MASKA /32. bacpypes3 si vedle unicastu otvírá i broadcastový socket.
    Na loopbacku se adresa jako 127.255.255.255 nedá přiřadit a aplikace
    vůbec nenaběhne. S /32 žádný broadcast nevzniká. Discovery (Who-Is) tím
    pádem nefunguje, což nevadí — dispečink čte přímou adresou.
  * VLASTNÍ UDP PORT pro každé zařízení. Dvě BACnet aplikace na jednom
    portu koexistovat nemůžou, a dispečink je taky BACnet zařízení.
"""

import asyncio
import logging

import BAC0
from BAC0.core.devices.local.factory import (
    ObjectFactory, analog_input, analog_value, binary_value,
)

import bacnet_points as points
import registers as regs

MASK = 32          # viz poznámka o broadcastu v hlavičce


def _quiet():
    """BAC0 a bacpypes3 jinak zaplaví výpis simulátoru."""
    for name in ("BAC0", "bacpypes3", "BAC0.core", "BAC0.tasks"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


class BacnetDevice:
    """
    Jedno simulované BACnet zařízení.

    Rozhraní je schválně stejné jako u Modbus kontextu v simulator.py:
    publish() vystaví měřené hodnoty, read_setpoints() vrátí, co do zařízení
    zapsal dispečink, clear_point() vynuluje samovynulovací registr.
    """

    def __init__(self, dev, host="127.0.0.1"):
        self.dev = dev
        self.host = host
        self.app = None
        self.objects = {}        # (typ, instance) -> objekt bacpypes3
        self.holding = regs.by_key(regs.DEVICE_TYPES[dev.type]["holding"])

    async def start(self):
        _quiet()
        ObjectFactory.clear_objects()

        last = None
        for p in points.ANALOG_INPUTS:
            last = analog_input(instance=p["instance"], name=p["name"],
                                description=p["description"], presentValue=0.0,
                                properties={"units": p["units"]})
        for p in points.ANALOG_VALUES:
            # Objekty nejsou commandable schválně: zápis pak jde rovnou do
            # presentValue a zařízení si ho může samo přepsat. To je potřeba
            # u kvitovacího bodu, který se po provedení vynuluje sám.
            default = self.holding[p["key"]]["default"]
            last = analog_value(instance=p["instance"], name=p["name"],
                                description=p["description"],
                                presentValue=float(default),
                                is_commandable=False,
                                properties={"units": p["units"]})
        for p in points.BINARY_VALUES:
            last = binary_value(instance=p["instance"], name=p["name"],
                                description=p["description"],
                                presentValue="inactive")

        self.app = BAC0.lite(ip=f"{self.host}/{MASK}", port=self.dev.port,
                             deviceId=self.dev.bacnet_id,
                             localObjName=self.dev.id)
        last.add_objects_to_application(self.app)
        await asyncio.sleep(1.0)          # ať se stack stihne rozběhnout

        app = self.app.this_application.app
        for oid, obj in app.objectIdentifier.items():
            self.objects[str(oid)] = obj
        return self

    # -- strana zařízení: vystavit měření --------------------------------------
    def _obj(self, kind, instance):
        """Objekty má bacpypes3 pod klíčem tvaru "analog-input,3"."""
        return self.objects.get(f"{kind},{instance}")

    def publish(self, values):
        """Vystaví spočítané hodnoty do BACnet objektů."""
        for p in points.ANALOG_INPUTS:
            obj = self._obj("analog-input", p["instance"])
            if obj is not None:
                obj.presentValue = float(values.get(p["key"], 0.0))

        word = int(values.get(points.ALARM_KEY, 0))
        for p in points.BINARY_VALUES:
            obj = self._obj("binary-value", p["instance"])
            if obj is not None:
                obj.presentValue = "active" if word & (1 << p["bit"]) else "inactive"

    def read_setpoints(self):
        """Přečte, co do zařízení zapsal dispečink; nesmysly nahradí výchozími."""
        out = {}
        for p in points.ANALOG_VALUES:
            reg = self.holding[p["key"]]
            obj = self._obj("analog-value", p["instance"])
            value = float(obj.presentValue) if obj is not None else reg["default"]
            out[p["key"]] = value if reg["min"] <= value <= reg["max"] else reg["default"]
        return out

    def set_point(self, key, value):
        """Nastaví žádanou hodnotu ze strany zařízení."""
        p = points.ANALOG_VALUE_BY_KEY.get(key)
        obj = self._obj("analog-value", p["instance"]) if p else None
        if obj is not None:
            obj.presentValue = float(value)

    def clear_point(self, key):
        """Vynuluje samovynulovací bod (kvitování poruchy)."""
        self.set_point(key, 0.0)

    def stop(self):
        if self.app is not None:
            try:
                self.app.disconnect()
            except Exception:
                pass

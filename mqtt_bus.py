"""
Sběrnice zpráv — jediné místo, kde je popsané, co teče kterým tématem.

Stejná filozofie jako registers.py: smlouva odděleně od kódu. Kdo publikuje
a kdo odebírá, si sem chodí pro téma a pro tvar zprávy, takže se obě strany
nemůžou rozejít.

TÉMATA

    factory/<zařízení>/sensors     měřené hodnoty      {klíč: číslo}
    factory/<zařízení>/setpoints   žádané hodnoty      {klíč: číslo}
    factory/<zařízení>/status      dostupnost          {"online": bool, ...}
    factory/gateway/status         život gatewaye      {"online": bool, ...}

PROČ TŘI TÉMATA A NE JEDNO

Měření a žádané hodnoty se posílají zvlášť, protože se mění jinak často a
jinak se s nimi zachází: měření teče pořád, žádanou hodnotu změní člověk
jednou za týden.

Dostupnost má vlastní téma, protože pásmo necitlivosti tlumí data, ne život
zařízení. Kdyby se mlčení dalo vysvětlit tím, že se hodnota nezměnila,
nedostupný regulátor by nebyl k rozeznání od klidného provozu. Status se
proto posílá v každém kole a je RETAINED — kdo se připojí, hned ví, na čem je.

POSLEDNÍ VŮLE (last will) je zpráva, kterou broker rozešle za klienta, když
spojení spadne bez rozloučení. Gateway ji má nastavenou na "offline", takže
dispečink pozná spadlý gateway i tehdy, když se neodhlásil.
"""

import json

HOST = "127.0.0.1"
PORT = 1883
KEEPALIVE = 30

PREFIX = "factory"
GATEWAY = "gateway"

SENSORS = "sensors"
SETPOINTS = "setpoints"
STATUS = "status"

#: QoS 0 pro měření — data tečou pořád a ztráta jedné zprávy nic neznamená,
#: další je za chvíli. QoS 1 pro stav a žádané hodnoty, kde na doručení záleží.
QOS_DATA = 0
QOS_STATE = 1


def topic(device_id, kind):
    return f"{PREFIX}/{device_id}/{kind}"


def gateway_topic():
    return f"{PREFIX}/{GATEWAY}/{STATUS}"


def subscription():
    """Co si odebírá dispečink — všechno pod prefixem."""
    return f"{PREFIX}/#"


def parse(topic_name):
    """
    Rozebere téma na (zařízení, druh). Vrací (None, None), když téma
    do téhle smlouvy nepatří.
    """
    parts = topic_name.split("/")
    if len(parts) != 3 or parts[0] != PREFIX:
        return None, None
    return parts[1], parts[2]


def encode(payload):
    return json.dumps(payload, separators=(",", ":"))


def decode(raw):
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def connect(client_id, on_message=None, will_topic=None, will_payload=None,
            host=HOST, port=PORT):
    """
    Připojí klienta k brokeru a rozjede jeho smyčku na vlastním vlákně.

    paho si drží vlastní vlákno (loop_start), takže se nepletе do asyncio
    smyčky dispečinku ani simulátoru. Zprávy se z něj předávají přes
    on_message, které musí být rychlé — dlouhý výpočet by zdržel příjem.
    """
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if will_topic:
        client.will_set(will_topic, encode(will_payload or {"online": False}),
                        qos=QOS_STATE, retain=True)
    if on_message:
        client.on_message = on_message
    client.connect(host, port, KEEPALIVE)
    client.loop_start()
    return client

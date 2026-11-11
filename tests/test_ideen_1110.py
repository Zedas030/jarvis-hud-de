"""
The seven ideas of 10.11.2026 evening: why something switched, where things are, birthdays, what Jarvis did
today, the night's ghosts, airing and print ideas. HA, weather and browser faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_ideen_1110 -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import shutil
import unittest
from unittest import mock

import activity
import airing
import birthdays
import ha_why
import memory
import night_report
import places
import print_ideas
import router

UTC = dt.timezone.utc


def ask(text):
    return router._local(text, router.LOCAL_HANDLERS)[0]


class Vault(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(memory.VAULT, ignore_errors=True)
        memory.MEMORY_DIR.mkdir(parents=True, exist_ok=True)


class Places(Vault):
    def test_put_and_find_with_original_spelling(self):
        self.assertIn("blaue Kiste im Flur", ask("Ich hab den Ersatzschlüssel in die blaue Kiste im Flur gelegt"))
        self.assertIn("in die blaue Kiste im Flur", ask("Wo ist der Ersatzschlüssel?"))

    def test_new_place_replaces_old(self):
        ask("Merk dir, der Reisepass liegt im Schreibtisch")
        ask("Der Reisepass liegt jetzt im Tresor")
        self.assertEqual(len(places.find("reisepass")), 1)
        self.assertIn("Tresor", ask("Wo liegt der Reisepass?"))

    def test_plain_statements_and_other_questions_stay_out(self):
        self.assertIsNone(places.handle(" das licht ist im wohnzimmer an "))
        self.assertIsNone(places.handle(" der schlüssel liegt im flur "))      # no "merk dir" / "jetzt"
        with mock.patch("jarvis_profile.USER_NAME", "Alex Muster"):
            self.assertIsNone(places.handle(" wo ist alex "))
        self.assertFalse(places._bullets())


class Birthdays(Vault):
    def setUp(self):
        super().setUp()
        p = memory.MEMORY_DIR / "Personen"
        p.mkdir()
        (p / "Anna.md").write_text("# Anna\n\n- [[Anna]] hat am 3. Mai Geburtstag _(abgeleitet, 10.11.2026)_\n"
                                   "- Mag Tulpen\n- Liest gern Krimis\n- Wohnt in Potsdam\n", encoding="utf-8")
        (memory.MEMORY_DIR / "Wissen.md").write_text("# Wissen\n\n- Ben hat am 04.05.1990 Geburtstag\n", encoding="utf-8")

    def test_people_and_upcoming(self):
        ppl = birthdays.people()
        self.assertEqual((ppl["Anna"]["day"], ppl["Anna"]["month"]), (3, 5))
        self.assertEqual(ppl["Ben"]["year"], 1990)
        soon = birthdays.upcoming(5, dt.date(2027, 5, 1))
        self.assertEqual([(n, a) for _, n, a in soon], [("Anna", None), ("Ben", 37)])

    def test_day_before_once_with_likes(self):
        said = []
        with mock.patch("events.publish", lambda t, text, **kw: said.append(text)):
            out = birthdays.step(dt.datetime(2027, 5, 2, 18, 30))
            birthdays.step(dt.datetime(2027, 5, 2, 19, 30))
        self.assertEqual(len(said), 1)
        self.assertIn("Morgen hat Anna Geburtstag", out[0])
        self.assertIn("Mag Tulpen, Liest gern Krimis", out[0])
        self.assertNotIn("Potsdam", out[0])

    def test_router(self):
        self.assertIn("3.5.", birthdays.handle(" wann hat anna geburtstag "))


class PrintIdeas(Vault):
    def test_add_read_done(self):
        self.assertIn("Kabelhalter für den Schreibtisch", ask("Merk dir als Druckidee: Kabelhalter für den Schreibtisch"))
        self.assertIn("Kabelhalter", ask("Was wollte ich noch drucken?"))
        self.assertIn("gestrichen", ask("Der Kabelhalter für den Schreibtisch ist gedruckt"))
        self.assertIn("Noch keine", ask("Welche Druckideen habe ich?"))

    def test_makerworld_opens_search(self):
        with mock.patch("jarvis_mcp._open") as op:
            self.assertIn("MakerWorld", ask("Such einen Kabelhalter auf MakerWorld"))
        self.assertIn("makerworld.com/de/search/models?keyword=Kabelhalter", op.call_args[0][0])


def entry(when, state=None, **ctx):
    e = {"when": when.isoformat()}
    if state:
        e["state"] = state
    e.update(ctx)
    return e


class Why(unittest.TestCase):
    def test_chain_back_to_the_sensor(self):
        t = dt.datetime(2026, 11, 10, 22, 12, tzinfo=UTC)
        books = {
            "light.licht": [entry(t, "on", context_event_type="automation_triggered", context_domain="automation",
                                  context_name="Jarvis: Licht Wohnzimmer folgt mir",
                                  context_source="state of input_select.room")],
            "input_select.room": [entry(t - dt.timedelta(seconds=3), "Wohnzimmer",
                                               context_event_type="automation_triggered", context_domain="automation",
                                               context_name="Wo bin ich",
                                               context_source="state of binary_sensor.praesenz_wz")],
            "binary_sensor.praesenz_wz": [entry(t - dt.timedelta(seconds=5), "on")]}
        states = {"light.licht": {"attributes": {"friendly_name": "LED"}},
                  "input_select.room": {"attributes": {"friendly_name": "Raum"}},
                  "binary_sensor.praesenz_wz": {"attributes": {"friendly_name": "Präsenz Wohnzimmer"}}}
        with mock.patch.object(ha_why, "logbook", lambda eid, start, end=None: books.get(eid, [])):
            text = ha_why.explain("light.licht", now=t + dt.timedelta(minutes=5), states=states)
        self.assertIn("LED ging", text)
        self.assertIn("„Jarvis: Licht Wohnzimmer folgt mir“, ausgelöst von „Raum“ (auf Wohnzimmer)", text)
        self.assertIn("„Wo bin ich“, ausgelöst von „Präsenz Wohnzimmer“ (an)", text)

    def test_no_context_means_device(self):
        self.assertIn("direkt am Gerät", ha_why.cause({"state": "on"}, {}))


class Night(unittest.TestCase):
    def test_counts_pulses_in_other_rooms_only(self):
        day = dt.date(2026, 11, 7)
        start = dt.datetime.combine(day, dt.time(0)).astimezone()
        at = lambda m: start + dt.timedelta(minutes=m)     # noqa: E731
        books = {
            night_report.ROOM_ENTITY: [entry(start - dt.timedelta(hours=1), "Schlafzimmer")],
            "binary_sensor.wz": [entry(at(i * 5), "on") for i in range(7)],
            "binary_sensor.sz": [entry(at(30), "on")],                      # that is him, he is in there
            "light.licht": [entry(at(40), "on"), entry(at(40), context_name="Jarvis: Licht Wohnzimmer folgt mir")]}
        states = {"binary_sensor.wz": {"attributes": {"device_class": "occupancy", "friendly_name": "Aufenthaltserkennung Wohnzimmer"}},
                  "binary_sensor.sz": {"attributes": {"device_class": "occupancy", "friendly_name": "Aufenthaltserkennung Schlafzimmer"}},
                  "light.licht": {"attributes": {"friendly_name": "LED"}},
                  night_report.ROOM_ENTITY: {"attributes": {}}}
        with mock.patch.object(ha_why, "logbook", lambda eid, s, e=None: books.get(eid, [])):
            r = night_report.analyse(day, states)
        self.assertEqual(r["ghosts"], [("Aufenthaltserkennung Wohnzimmer", 7, "wohnzimmer")])
        text = night_report.spoken(r)
        self.assertIn("7-mal", text)
        self.assertIn("LED ging dabei 1-mal an (Jarvis: Licht Wohnzimmer folgt mir)", text)
        self.assertEqual(night_report.spoken({"ghosts": [("X", 2, "bad")], "lights": []}), "")   # too little to say


class Airing(unittest.TestCase):
    STATES = {"sensor.h_sz": {"state": "66", "attributes": {"device_class": "humidity", "friendly_name": "Schlafzimmer Luftfeuchtigkeit"}},
              "sensor.t_sz": {"state": "19", "attributes": {"device_class": "temperature", "friendly_name": "Schlafzimmer Temperatur"}},
              "sensor.h_zelt": {"state": "80", "attributes": {"device_class": "humidity", "friendly_name": "Zelt Luftfeuchtigkeit"}},
              "sensor.t_zelt": {"state": "25", "attributes": {"device_class": "temperature", "friendly_name": "Zelt Temperatur"}}}

    def test_absolute_humidity(self):
        self.assertAlmostEqual(airing.absolute(20, 50), 8.6, places=1)

    def test_rooms_and_advice(self):
        rooms = airing.rooms(self.STATES)
        self.assertEqual(list(rooms), ["schlafzimmer"])                       # the tent has no room word
        cold = airing.advice(rooms, {"temp": 4, "rh": 90})[0]
        self.assertTrue(cold[4])                                              # 90 % at 4 °C is drier than 66 % at 19 °C
        muggy = airing.advice(rooms, {"temp": 24, "rh": 70})[0]
        self.assertFalse(muggy[4])

    def test_wet_days(self):
        now = dt.datetime(2026, 11, 10, 12, 0).astimezone()
        series = [(now - dt.timedelta(days=4), 70.0)]
        self.assertEqual(airing.wet_days(series, now), 3)
        series.append((now - dt.timedelta(days=2) + dt.timedelta(hours=-6), 55.0))
        self.assertLess(airing.wet_days(series, now), 3)


class Activity(unittest.TestCase):
    def test_quiet_day(self):
        with mock.patch.object(activity, "requests", return_value=(0, 0)), \
                mock.patch.object(activity, "notices", return_value=[]), \
                mock.patch.object(activity, "saved", return_value=[]), \
                mock.patch.object(activity, "proposed", return_value=[]), \
                mock.patch.object(activity, "actions", return_value=[]):
            self.assertIn("ruhig", activity.report())

    def test_busy_day(self):
        at = dt.datetime.now().replace(hour=16, minute=40).timestamp()
        with mock.patch.object(activity, "requests", return_value=(14, 9)), \
                mock.patch.object(activity, "notices", return_value=[(at, "appliance", "Sir, die Waschmaschine ist fertig.")]), \
                mock.patch.object(activity, "saved", return_value=["Anna mag Tulpen"]), \
                mock.patch.object(activity, "proposed", return_value=[{"status": "open", "title": "Container x neu starten"}]), \
                mock.patch.object(activity, "actions", return_value=[{"tool": "home_control"}]):
            text = activity.handle(" was hast du heute so gemacht ")
        self.assertIn("14 Anfragen, 9 davon selbst", text)
        self.assertIn("16:40 „die Waschmaschine ist fertig.“", text)
        self.assertIn("Gemerkt: Anna mag Tulpen", text)
        self.assertIn("offen: Container x neu starten", text)
        self.assertIn("home_control", text)


if __name__ == "__main__":
    unittest.main()

import isolate  # noqa: F401 — first: temp state, no house

import unittest

import away_check


def st(eid, state="playing", name=None):
    return eid, {"entity_id": eid, "state": state, "attributes": {"friendly_name": name or eid}}


class StillOn(unittest.TestCase):
    def test_spotify_account_never_counts(self):
        # HA's Spotify entity is the whole account: "playing" may be the phone on the road.
        states = dict([st("media_player.spotify_konto", name="Spotify"), st("media_player.tv", name="TV")])
        self.assertEqual(away_check.still_on(states, {}), [("media_player.tv", "TV")])

    def test_media_skip_leaves_out_players_ha_reports_itself(self):
        states = dict([st("media_player.sonos"), st("media_player.tv", name="TV")])
        self.assertEqual(away_check.still_on(states, {"media_skip": r"^media_player\.sonos$"}),
                         [("media_player.tv", "TV")])


if __name__ == "__main__":
    unittest.main()

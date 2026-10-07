"""Sérialiseurs JSON : nouveaux champs images / détails de partie."""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.serializers import match_row, parse_items, parse_spells, player_public
from app.db.models import MatchParticipant, Player, Queue, RankSnapshot
from app.riot import ddragon

DD = "https://ddragon.leagueoflegends.com/cdn"
CD = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default"


class TestParsers:
    def test_parse_items(self):
        assert parse_items("[3031,3006,0,0,0,0,3340]") == [3031, 3006, 0, 0, 0, 0, 3340]
        assert parse_items(None) == [0] * 7
        assert parse_items("") == [0] * 7
        assert parse_items("pas du json") == [0] * 7
        assert parse_items("{}") == [0] * 7
        assert parse_items("[3031]") == [3031, 0, 0, 0, 0, 0, 0]  # complété à 7
        assert parse_items("[1,2,3,4,5,6,7,8]") == [1, 2, 3, 4, 5, 6, 7]  # tronqué à 7
        assert parse_items('[3031,"x",null,-5,0,0,3340]') == [3031, 0, 0, 0, 0, 0, 3340]

    def test_parse_spells(self):
        assert parse_spells("4,14") == [4, 14]
        assert parse_spells(" 4 , 11 ") == [4, 11]
        assert parse_spells(None) == []
        assert parse_spells("") == []
        assert parse_spells("4,x") == [4]


def _participant(**overrides) -> MatchParticipant:
    data = dict(
        match_id="EUW1_1",
        player_id=1,
        queue=Queue.SOLO,
        game_start=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        game_duration=1800,
        champion_name="Wukong",
        position="JUNGLE",
        team_side=200,
        win=True,
        kills=5,
        deaths=2,
        assists=7,
        cs=180,
        items="[3031,3006,0,0,0,0,3340]",
        spells="4,11",
        champ_level=16,
        kill_participation=66.7,
    )
    data.update(overrides)
    return MatchParticipant(**data)


class TestMatchRow:
    def test_new_fields(self):
        version = ddragon.CURRENT_VERSION
        row = match_row(_participant(), Player(display_name="Mike", game_name="Mike", tag_line="EUW"))
        assert row["champion_icon_url"] == f"{DD}/{version}/img/champion/MonkeyKing.png"
        assert row["champion_splash_url"] == f"{DD}/img/champion/splash/MonkeyKing_0.jpg"
        assert row["champion_loading_url"] == f"{DD}/img/champion/loading/MonkeyKing_0.jpg"
        assert row["position_icon_url"] == f"{CD}/svg/position-jungle.svg"
        assert row["team_side"] == 200
        assert row["champ_level"] == 16
        assert row["kill_participation"] == 66.7
        assert row["items"] == [3031, 3006, 0, 0, 0, 0, 3340]
        assert row["item_urls"] == [
            f"{DD}/{version}/img/item/3031.png", f"{DD}/{version}/img/item/3006.png",
            None, None, None, None, f"{DD}/{version}/img/item/3340.png",
        ]
        assert row["spells"] == [4, 11]
        assert row["spell_urls"] == [f"{DD}/{version}/img/spell/SummonerFlash.png", f"{DD}/{version}/img/spell/SummonerSmite.png"]

    def test_missing_details_are_none_or_empty(self):
        row = match_row(
            _participant(items=None, spells=None, champ_level=None, kill_participation=None, team_side=None, position=None),
            None,
        )
        assert row["items"] == [0] * 7 and row["item_urls"] == [None] * 7
        assert row["spells"] == [] and row["spell_urls"] == []
        assert row["champ_level"] is None and row["kill_participation"] is None
        assert row["team_side"] is None and row["position_icon_url"] is None
        assert row["opgg_url"] is None


class TestPlayerPublic:
    def test_rank_images(self):
        player = Player(id=1, display_name="Mike")
        snapshot = RankSnapshot(player_id=1, tier="PLATINUM", rank="II", lp=40)
        data = player_public(player, snapshot)
        assert data["rank_emblem_url"] == f"{CD}/images/ranked-emblem/emblem-platinum.png"
        assert data["rank_crest_url"] == f"{CD}/images/ranked-mini-crests/platinum.svg"

    def test_unranked_has_no_images(self):
        data = player_public(Player(id=1, display_name="Mike"), None)
        assert data["rank_label"] == "Unranked"
        assert data["rank_emblem_url"] is None and data["rank_crest_url"] is None

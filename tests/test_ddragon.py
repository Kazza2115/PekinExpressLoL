"""Helpers d'URL Data Dragon / Community Dragon (purs, sans réseau)."""

from __future__ import annotations

import pytest

from app.riot import ddragon

DD = "https://ddragon.leagueoflegends.com/cdn"
CD = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default"


class TestChampionImages:
    def test_icon_splash_loading(self):
        assert ddragon.champion_icon_url("14.24.1", "Ahri") == f"{DD}/14.24.1/img/champion/Ahri.png"
        assert ddragon.champion_splash_url("Ahri") == f"{DD}/img/champion/splash/Ahri_0.jpg"
        assert ddragon.champion_loading_url("Ahri") == f"{DD}/img/champion/loading/Ahri_0.jpg"

    def test_special_names_go_through_image_name(self):
        assert ddragon.champion_splash_url("Wukong") == f"{DD}/img/champion/splash/MonkeyKing_0.jpg"
        assert ddragon.champion_loading_url("Kai'Sa") == f"{DD}/img/champion/loading/Kaisa_0.jpg"
        assert ddragon.champion_splash_url("Nunu & Willump") == f"{DD}/img/champion/splash/Nunu_0.jpg"

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_empty_is_none(self, name):
        assert ddragon.champion_splash_url(name) is None
        assert ddragon.champion_loading_url(name) is None


class TestItemsAndSpells:
    def test_item_icon(self):
        assert ddragon.item_icon_url("14.24.1", 3031) == f"{DD}/14.24.1/img/item/3031.png"
        assert ddragon.item_icon_url("14.24.1", "3006") == f"{DD}/14.24.1/img/item/3006.png"

    @pytest.mark.parametrize("item", [None, 0, -1, "abc"])
    def test_item_empty_slot_is_none(self, item):
        assert ddragon.item_icon_url("14.24.1", item) is None

    def test_item_version_fallback(self):
        assert ddragon.item_icon_url("", 3031) == f"{DD}/{ddragon.CURRENT_VERSION}/img/item/3031.png"

    @pytest.mark.parametrize(
        ("spell_id", "name"),
        [
            (1, "SummonerBoost"), (3, "SummonerExhaust"), (4, "SummonerFlash"), (6, "SummonerHaste"),
            (7, "SummonerHeal"), (11, "SummonerSmite"), (12, "SummonerTeleport"), (13, "SummonerMana"),
            (14, "SummonerDot"), (21, "SummonerBarrier"), (32, "SummonerSnowball"),
        ],
    )
    def test_spell_icon(self, spell_id, name):
        assert ddragon.spell_icon_url("14.24.1", spell_id) == f"{DD}/14.24.1/img/spell/{name}.png"

    @pytest.mark.parametrize("spell_id", [None, 0, 99, "x"])
    def test_unknown_spell_is_none(self, spell_id):
        assert ddragon.spell_icon_url("14.24.1", spell_id) is None


class TestRankAndPosition:
    def test_rank_emblem_and_crest(self):
        assert ddragon.rank_emblem_url("GOLD") == f"{CD}/images/ranked-emblem/emblem-gold.png"
        assert ddragon.rank_emblem_url("challenger") == f"{CD}/images/ranked-emblem/emblem-challenger.png"
        assert ddragon.rank_mini_crest_url("IRON") == f"{CD}/images/ranked-mini-crests/iron.svg"
        assert ddragon.rank_mini_crest_url("Grandmaster") == f"{CD}/images/ranked-mini-crests/grandmaster.svg"

    @pytest.mark.parametrize("tier", [None, "", "UNRANKED", "WOOD"])
    def test_unranked_is_none(self, tier):
        assert ddragon.rank_emblem_url(tier) is None
        assert ddragon.rank_mini_crest_url(tier) is None

    @pytest.mark.parametrize(
        ("position", "slug"),
        [("TOP", "top"), ("JUNGLE", "jungle"), ("MIDDLE", "middle"), ("BOTTOM", "bottom"), ("UTILITY", "utility"), ("top", "top")],
    )
    def test_position_icon(self, position, slug):
        assert ddragon.position_icon_url(position) == f"{CD}/svg/position-{slug}.svg"

    @pytest.mark.parametrize("position", [None, "", "Invalid", "SUPPORT"])
    def test_unknown_position_is_none(self, position):
        assert ddragon.position_icon_url(position) is None

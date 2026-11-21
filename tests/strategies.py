"""Hypothesis strategies compiled from mechfight.schema.json.

Walks the schema subset it uses ($ref, anyOf/oneOf, type, const, enum, properties/required,
additionalProperties, items, minItems/maxItems): every generated message is schema-valid.
Two number modes:
  game  - numbers in map/game scale (positions within +-1200 m, ticks are non-negative integers, ...),
          plus the protocol facts the schema can't express: you never appear in your own `mechs`.
  wild  - any finite JSON number (what the schema literally allows), for crash-proofing only.
"""
import json
import math

from hypothesis import strategies as st

from conftest import SCHEMA

ROOT = json.load(open(SCHEMA))
DEFS = ROOT["definitions"]

# Field-name -> number strategy, game mode. Anything not listed gets GAME_NUMBER.
GAME_NUMBER = st.floats(-1200, 1200, allow_nan=False, allow_infinity=False)
GAME_FIELDS = {
    "tick": st.integers(0, 9600),
    "t": st.floats(0, 480),
    "alive": st.integers(1, 16),
    "id": st.integers(0, 30000),
    "hp": st.floats(0, 3000),
    "max_hp": st.floats(1, 3000),
    "energy": st.floats(0, 100),
    "max_energy": st.floats(1, 200),
    "cubes": st.integers(0, 30),
    "radius": st.floats(0, 600),
    "next_radius": st.floats(0, 600),
    "shrink_start": st.floats(0, 480),
    "shrink_end": st.floats(0, 480),
    "dps": st.floats(0, 300),
    "age": st.floats(0, 1),
    "expires": st.floats(0, 480),
    "cooldown": st.floats(0, 3),
    "amount": st.floats(0, 500),
    "bearing": st.floats(-math.pi, math.pi),
    # Velocities (mechs walk ~12, boost ~20, knock-back 16, fall/jet up to ~40 m/s). Bullets are faster,
    # but the bot ignores projectile velocities; an actor faster than a bullet is not a game situation.
    "vel": st.floats(-40, 40),
}
WILD_NUMBER = st.one_of(st.integers(-2**53, 2**53),
                        st.floats(allow_nan=False, allow_infinity=False))
MAX_LIST = {"game": 5, "wild": 3}


def _number(mode, field):
    if mode == "wild":
        return WILD_NUMBER
    return GAME_FIELDS.get(field, GAME_NUMBER)


def compile_schema(s, mode, field=None):
    if "$ref" in s:
        name = s["$ref"].split("/")[-1]
        return st.deferred(lambda: compile_schema(DEFS[name], mode, field))
    alts = s.get("anyOf") or s.get("oneOf")
    if alts:
        return st.one_of([compile_schema(a, mode, field) for a in alts])
    if "const" in s:
        return st.just(s["const"])
    if "enum" in s:
        return st.sampled_from(s["enum"])
    t = s.get("type")
    if isinstance(t, list):
        return st.one_of([compile_schema({**s, "type": x}, mode, field) for x in t])
    if t == "object" or "properties" in s:
        props, req = s.get("properties", {}), set(s.get("required", []))
        required = {k: compile_schema(v, mode, k) for k, v in props.items() if k in req}
        optional = {k: compile_schema(v, mode, k) for k, v in props.items() if k not in req}
        base = st.fixed_dictionaries(required, optional=optional)
        extra = s.get("additionalProperties")
        if isinstance(extra, dict) and not props:
            return st.dictionaries(st.text(max_size=12), compile_schema(extra, mode, None), max_size=4)
        return base
    if t == "array":
        items = s.get("items", {})
        lo = s.get("minItems", 0)
        hi = s.get("maxItems", lo + MAX_LIST[mode])
        if field in ("pos", "vel", "center", "next_center", "at", "a", "b"):
            return st.lists(compile_schema(items, mode, field), min_size=lo, max_size=hi)
        if field == "aim" and mode == "game":
            return st.tuples(st.floats(-math.pi, math.pi), st.floats(-1.2, 1.2)).map(list)
        return st.lists(compile_schema(items, mode, None), min_size=lo, max_size=hi)
    if t == "number":
        return _number(mode, field)
    if t == "integer":
        return st.integers(0, 10**6) if mode == "game" else st.integers()
    if t == "string":
        return st.text(max_size=16)
    if t == "boolean":
        return st.booleans()
    if t == "null":
        return st.none()
    return st.nothing()


SIGHT_M = 300.0  # vision.range 200 m, plus up to memory_s 5 s of tracking after a mech leaves view


def _game_fixups(o):
    """Protocol facts the schema doesn't encode."""
    me = o["self"]["id"] = o["self"]["id"] % 16
    sx, sy, sz = o["self"]["pos"]
    for m in o["mechs"]:
        # Everything in `mechs` is seen (or tracked for memory_s): within ~SIGHT_M of us, +-60 m in height.
        dx, dy, dz = m["pos"][0] / 4, m["pos"][1] / 4, m["pos"][2] / 20
        r = math.hypot(dx, dy)
        if r > SIGHT_M:
            dx, dy = dx * SIGHT_M / r, dy * SIGHT_M / r
        m["pos"] = [sx + dx, sy + dy, sz + dz]
        if m["kind"] == "mech":
            m["id"] = m["id"] % 16
            if m["id"] == me:
                m["id"] = (me + 1) % 16  # the server never lists you in your own `mechs`
        else:
            m["id"] = 100 + m["id"] % 100
    z = o["zone"]
    if z["shrink_end"] < z["shrink_start"]:
        z["shrink_start"], z["shrink_end"] = z["shrink_end"], z["shrink_start"]
    return o


OBSERVATION_SCHEMA = {"$ref": "#/definitions/Observation"}
game_observations = compile_schema(OBSERVATION_SCHEMA, "game").map(_game_fixups)
wild_observations = compile_schema(OBSERVATION_SCHEMA, "wild")


def with_offer(obs_strategy):
    """Observations that always carry an upgrade offer with 1-3 choices (as the protocol sends)."""
    choice = compile_schema({"$ref": "#/definitions/OfferChoice"}, "game")
    offer = st.fixed_dictionaries({"id": st.integers(0, 50), "expires": st.floats(0, 480),
                                   "choices": st.lists(choice, min_size=1, max_size=3)})
    return st.tuples(obs_strategy, offer).map(lambda p: {**p[0], "offer": p[1]})

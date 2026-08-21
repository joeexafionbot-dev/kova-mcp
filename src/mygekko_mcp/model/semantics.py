"""Parsers for the myGEKKO value grammars found in ``/api/v1/var`` discovery.

Two grammars (see docs/query-api-notes.md, verified against the wiki example JSON):

1. ``sumstate.format`` — read semantics. Semicolon-separated *named fields*::

       currentState enum[0=off,1=on](); dimLevel float[0.00,100.0](%); ...

   Each field is ``<name> <KIND[inner](unit)>``. A live status value like
   ``"0;60;0;;0"`` is decoded by zipping its ``;``-split parts onto these fields.

   Some fields are *device-model dependent* and carry variants introduced by ``#``::

       workingMode #standard:enum[1=off,8=comfort,...]#knx:enum[0=auto,...]()

2. ``scmd.format`` — write semantics. ``<grammar> (<labels>)`` where grammar is
   pipe-separated command tokens whose example args illustrate the syntax::

       -2|-1|0|1|2|T|P55.4|S32.4 (Hold_down|Down|Stop|Up|Hold_up|Toggle|Position|Angle)

   Token ``P55.4`` means: prefix ``P`` + a float argument (the ``55.4`` is only an
   example). ``0`` / ``T`` are literal tokens with no argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# <KIND>[<inner>](<unit>)   e.g.  enum[0=off,1=on]()   float[0.00,100.0](%)
# The (unit) part is optional: in device-model variant fields only the last
# variant carries the trailing "()".
_TYPESPEC_RE = re.compile(r"^(?P<kind>\w+)\[(?P<inner>.*)\](?:\((?P<unit>[^)]*)\))?\s*$")
# A command token: leading letters/sign prefix, optional numeric example arg.
_CMD_RE = re.compile(r"^(?P<prefix>[A-Za-z]+|-?\d+|-|T)(?P<arg>-?\d+(?:\.\d+)?)?$")


@dataclass
class FieldSpec:
    """One decoded field of a ``sumstate.format`` string."""

    name: str
    kind: str  # enum | float | int | string | null | unknown
    unit: str = ""
    enum_map: dict[int, str] = field(default_factory=dict)
    low: float | None = None
    high: float | None = None
    is_variant: bool = False  # device-model dependent (#variant:...)
    raw: str = ""

    def decode(self, raw_value: str) -> object:
        """Turn a raw status token into a friendlier Python value."""
        v = raw_value.strip()
        if v == "":
            return None
        try:
            if self.kind == "enum":
                return self.enum_map.get(int(float(v)), v)
            if self.kind == "float":
                return float(v)
            if self.kind == "int":
                return int(float(v))
        except (ValueError, TypeError):
            return v
        return v


@dataclass
class CommandSpec:
    """One command token of a ``scmd.format`` string."""

    token: str  # example token as documented, e.g. "P55.4", "D100", "0", "T"
    prefix: str  # syntactic prefix to send, e.g. "P", "D", "0", "T"
    takes_arg: bool  # whether a value must be appended to the prefix
    label: str = ""  # human label from the parenthesised part

    def describe(self) -> str:
        if self.takes_arg:
            return (
                f"{self.prefix}<value>  ({self.label})" if self.label else f"{self.prefix}<value>"
            )
        return f"{self.prefix}  ({self.label})" if self.label else self.prefix


def _parse_typespec(spec: str) -> tuple[str, str, dict[int, str], float | None, float | None]:
    """Parse ``KIND[inner](unit)`` into (kind, unit, enum_map, low, high)."""
    m = _TYPESPEC_RE.match(spec.strip())
    if not m:
        return "unknown", "", {}, None, None
    kind = m.group("kind")
    inner = m.group("inner")
    unit = m.group("unit") or ""
    enum_map: dict[int, str] = {}
    low = high = None
    if kind == "enum":
        for pair in inner.split(","):
            k, _, val = pair.partition("=")
            k = k.strip()
            if k.lstrip("-").isdigit():
                enum_map[int(k)] = val.strip()
    elif kind in ("float", "int"):
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) >= 2:
            low = _to_float(parts[0])
            high = _to_float(parts[1])
    return kind, unit, enum_map, low, high


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None  # e.g. "..." open-ended range


def parse_sumstate_format(fmt: str | None) -> list[FieldSpec]:
    """Parse a ``sumstate.format`` string into an ordered list of FieldSpec."""
    if not fmt:
        return []
    fields: list[FieldSpec] = []
    for chunk in fmt.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, rest = chunk.partition(" ")
        rest = rest.strip()
        # `globals` leaves are bare typespecs with no field name (e.g.
        # "float[-100,100](°C)"). Detect: the "name" already contains "[".
        if "[" in name and not rest:
            name, rest = "", chunk.strip()
        if rest.startswith("#"):
            # Device-model dependent variant field: parse the first variant for a
            # best-effort enum map, but flag it and keep the raw form.
            first = rest.split("#", 2)[1] if "#" in rest else rest
            _, _, sub = first.partition(":")
            kind, unit, enum_map, low, high = _parse_typespec(sub)
            fields.append(
                FieldSpec(
                    name=name,
                    kind=kind,
                    unit=unit,
                    enum_map=enum_map,
                    low=low,
                    high=high,
                    is_variant=True,
                    raw=rest,
                )
            )
            continue
        kind, unit, enum_map, low, high = _parse_typespec(rest)
        fields.append(
            FieldSpec(
                name=name, kind=kind, unit=unit, enum_map=enum_map, low=low, high=high, raw=rest
            )
        )
    return fields


def parse_scmd_format(fmt: str | None) -> list[CommandSpec]:
    """Parse a ``scmd.format`` string into an ordered list of CommandSpec."""
    if not fmt or fmt.strip().lower() == "none":
        return []
    grammar, labels = _split_grammar_labels(fmt)
    tokens = [t.strip() for t in grammar.split("|") if t.strip()]
    label_list = [x.strip() for x in labels.split("|")] if labels else []
    specs: list[CommandSpec] = []
    for i, tok in enumerate(tokens):
        prefix, takes_arg = _classify_token(tok)
        label = label_list[i] if i < len(label_list) else ""
        specs.append(CommandSpec(token=tok, prefix=prefix, takes_arg=takes_arg, label=label))
    _resolve_literal_prefixes(specs)
    return specs


def _resolve_literal_prefixes(specs: list[CommandSpec]) -> None:
    """Reclassify repeated-prefix tokens as literals, in place.

    ``D100`` vs ``M8`` are syntactically identical (letter + number), but a
    prefix that appears on *several* tokens (roomtemps ``M1|M2|M8|M16``, pools
    ``C1|C2|C3``) is an enum of literal modes, not "prefix + value". See
    docs/query-api-notes.md §3. A unique prefix (``D100``, ``S22.5``, ``T100``)
    stays a value parameter.
    """
    counts: dict[str, int] = {}
    for s in specs:
        if s.takes_arg:
            counts[s.prefix] = counts.get(s.prefix, 0) + 1
    for s in specs:
        if s.takes_arg and counts[s.prefix] >= 2:
            s.prefix = s.token
            s.takes_arg = False


def _split_grammar_labels(fmt: str) -> tuple[str, str]:
    """Separate ``grammar (labels)`` — labels are optional."""
    fmt = fmt.strip()
    if fmt.endswith(")") and "(" in fmt:
        idx = fmt.rindex("(")
        return fmt[:idx].strip(), fmt[idx + 1 : -1].strip()
    return fmt, ""


def _classify_token(tok: str) -> tuple[str, bool]:
    """Return (prefix_to_send, takes_arg) for a command token.

    ``P55.4`` -> ("P", True); ``D100`` -> ("D", True); ``0`` -> ("0", False);
    ``-2`` -> ("-2", False); ``T`` -> ("T", False).

    myGEKKO's ``0..x`` placeholder notation uses a trailing literal ``x`` to mark
    a value slot, e.g. ``Mx`` (OperatingMode[0..x]) or ``Ax`` (plug behaviour) —
    those are ``prefix + value``, not literal tokens.
    """
    xm = re.match(r"^([A-Za-z]+)x$", tok)
    if xm:
        return xm.group(1), True
    m = _CMD_RE.match(tok)
    if not m:
        return tok, False
    prefix = m.group("prefix")
    arg = m.group("arg")
    # A pure number (e.g. "0", "-2", "16") is a literal command, not prefix+arg.
    if arg is not None and prefix.lstrip("-").isalpha():
        return prefix, True
    return tok, False


def interpret_value(fields: list[FieldSpec], value: str | list) -> dict[str, object]:
    """Zip a live status ``value`` onto its FieldSpecs.

    Accepts either the ``;``-joined string form or the ``format=array`` list form.
    Returns an ordered dict of field name -> {"raw", "value", "unit"}.
    """
    parts = value if isinstance(value, list) else str(value).split(";")
    out: dict[str, object] = {}
    for i, f in enumerate(fields):
        raw = parts[i] if i < len(parts) else ""
        raw_str = "" if raw is None else str(raw)
        out[f.name] = {"raw": raw_str, "value": f.decode(raw_str), "unit": f.unit}
    return out

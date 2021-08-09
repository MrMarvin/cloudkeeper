from functools import reduce
from typing import Callable

from parsy import string, regex, digit, generate, success, Parser

from core.model.graph_access import EdgeType
from core.query.model import Predicate, CombinedTerm, IsInstanceTerm, Part, Navigation, Query, FunctionTerm, IdTerm

whitespace: Parser = regex(r"\s*")


def make_parser(fn: Callable[[], Parser]) -> Parser:
    return generate(fn)


def lexeme(p: Parser) -> Parser:
    return whitespace >> p << whitespace


operationP = reduce(
    lambda x, y: x | y, [lexeme(string(a)) for a in ["<=", ">=", ">", "<", "==", "!=", "=~", "!~", "in", "not in"]]
)

functionP = reduce(lambda x, y: x | y, [lexeme(string(a)) for a in ["in_subnet", "has_desired_change"]])

preamblePropP = reduce(lambda x, y: x | y, [lexeme(string(a)) for a in ["edge_type"]])

lparenP = lexeme(string("("))
rparenP = lexeme(string(")"))
lbrackP = lexeme(string("["))
rbrackP = lexeme(string("]"))
gtP = lexeme(string(">"))
ltP = lexeme(string("<"))
colonP = lexeme(string(":"))
commaP = lexeme(string(","))
dotdotP = lexeme(string(".."))
equalsP = lexeme(string("="))
trueP = lexeme(string("true")).result(True)
falseP = lexeme(string("false")).result(False)
nullP = lexeme(string("null")).result(None)
integerP = digit.at_least(1).concat().map(int)
floatP = (digit.many() + string(".").result(["."]) + digit.many()).concat().map(float)
variableP = lexeme(regex("[A-Za-z][A-Za-z0-9.*\\[\\]]*"))
literalP = lexeme(regex("[A-Za-z][A-Za-z0-9]*"))

string_part = regex(r'[^"\\]+')
string_esc = string("\\") >> (
    string("\\")
    | string("/")
    | string('"')
    | string("b").result("\b")
    | string("f").result("\f")
    | string("n").result("\n")
    | string("r").result("\r")
    | string("t").result("\t")
    | regex(r"u[0-9a-fA-F]{4}").map(lambda s: chr(int(s[1:], 16)))
)
stringP = (string_part | string_esc).many().concat()
quotedStringP = lexeme(string('"') >> stringP << string('"'))


@make_parser
def array_parser() -> Parser:
    yield lbrackP
    elements = yield valueP.sep_by(commaP)
    yield rbrackP
    return elements


valueP = quotedStringP | floatP | integerP | array_parser | trueP | falseP | nullP


@make_parser
def predicate_term() -> Parser:
    name = yield variableP
    op = yield operationP
    value = yield valueP
    return Predicate(name, op, value, {})


@make_parser
def function_term() -> Parser:
    fn = yield functionP
    yield lparenP
    name = yield variableP
    args = yield (commaP >> valueP).many()
    yield rparenP
    return FunctionTerm(fn, name, args)


isinstance_term = lexeme(string("isinstance") >> lparenP >> quotedStringP << rparenP).map(IsInstanceTerm)
id_term = lexeme(string("id") >> lparenP >> quotedStringP << rparenP).map(IdTerm)

leafTermP = isinstance_term | id_term | predicate_term | function_term

boolOpP = lexeme(string("and") | string("or"))


@make_parser
def combined_term() -> Parser:
    left = yield simpleTermP
    result = left
    while True:
        op = yield boolOpP | success(None)
        if op is None:
            break
        right = yield simpleTermP
        result = CombinedTerm(result, op, right)
    return result


simpleTermP = (lparenP >> combined_term << rparenP) | leafTermP

# This can parse a complete term
term_parser = combined_term | simpleTermP


@make_parser
def range_parser() -> Parser:
    yield lbrackP
    start = yield integerP
    has_end = yield (colonP | commaP | dotdotP).optional()
    maybe_end = yield integerP.optional()
    yield rbrackP
    end = start if has_end is None else maybe_end if maybe_end is not None else Navigation.Max
    return start, end


@make_parser
def edge_definition() -> Parser:
    maybe_edge_type = yield literalP.optional()
    maybe_range = yield range_parser.optional()
    parsed_range = maybe_range if maybe_range else (1, 1)
    return parsed_range[0], parsed_range[1], maybe_edge_type


outP = lexeme(string("-") >> edge_definition << string("->")).map(lambda nav: Navigation(nav[0], nav[1], nav[2], "out"))
inP = lexeme(string("<-") >> edge_definition << string("-")).map(lambda nav: Navigation(nav[0], nav[1], nav[2], "in"))
inOutP = lexeme(string("-") >> edge_definition << string("-")).map(
    lambda nav: Navigation(nav[0], nav[1], nav[2], "inout")
)
navigation_parser = outP | inP | inOutP

pin_parser = lexeme(string("+")).optional().map(lambda x: x is not None)


@make_parser
def part_parser() -> Parser:
    term = yield term_parser
    yield whitespace
    nav = yield navigation_parser | success(None)
    pinned = yield pin_parser
    return Part(term, pinned, nav)


@make_parser
def preamble_value_parser() -> Parser:
    key = yield preamblePropP
    yield equalsP
    value = yield quotedStringP | literalP
    return key, value


preambleP: Parser = (preamble_value_parser.sep_by(commaP) << colonP).map(dict)


@make_parser
def query_parser() -> Parser:
    maybe_preamble = yield preambleP.optional()
    preamble = maybe_preamble if maybe_preamble else dict()
    parts = yield part_parser.many()
    edge_type = preamble.get("edge_type", EdgeType.default)
    if edge_type not in EdgeType.allowed_edge_types:
        raise AttributeError(f"Given edge_type {edge_type} is not available. Use one of {EdgeType.allowed_edge_types}")
    for part in parts:
        if part.navigation and not part.navigation.edge_type:
            part.navigation.edge_type = edge_type
    return Query(parts[::-1])


def parse_query(query: str) -> Query:
    return query_parser.parse(query)  # type: ignore

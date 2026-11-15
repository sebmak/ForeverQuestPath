"""
Tokenizer and recursive-descent parser for static Lua table literals used in
World of Warcraft: Forever addon data files (ForeverLevelingBiS, OpenMap, QuestieDB).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Token:
    """Represents one lexical token produced by the Lua lexer."""

    kind: str
    value: Any
    pos: int


def tokenize_lua(src: str) -> list[Token]:
    """Tokenizes a Lua source snippet containing tables, literals, and assignments."""
    tokens: list[Token] = []
    i = 0
    n = len(src)

    while i < n:
        ch = src[i]

        # Skip whitespace
        if ch in " \t\r\n":
            i += 1
            continue

        # Comments: --[[ ... ]] or -- ...
        if ch == "-" and i + 1 < n and src[i + 1] == "-":
            if i + 3 < n and src[i + 2 : i + 4] == "[[":
                end = src.find("]]", i + 4)
                i = n if end == -1 else end + 2
            else:
                end = src.find("\n", i + 2)
                i = n if end == -1 else end + 1
            continue

        # Long bracket strings [[ ... ]]
        if ch == "[" and i + 1 < n and src[i + 1] == "[":
            end = src.find("]]", i + 2)
            if end == -1:
                raise ValueError(f"Unterminated long string at pos {i}")
            tokens.append(Token("STRING", src[i + 2 : end], i))
            i = end + 2
            continue

        # Quoted strings "..." or '...'
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            buf: list[str] = []
            while j < n:
                c = src[j]
                if c == "\\":
                    j += 1
                    if j >= n:
                        break
                    esc = src[j]
                    if esc == "n":
                        buf.append("\n")
                    elif esc == "r":
                        buf.append("\r")
                    elif esc == "t":
                        buf.append("\t")
                    else:
                        buf.append(esc)
                    j += 1
                elif c == quote:
                    j += 1
                    break
                else:
                    buf.append(c)
                    j += 1
            tokens.append(Token("STRING", "".join(buf), i))
            i = j
            continue

        # Numbers (optional leading unary '-' handled at parse time or here when followed by digit)
        if ch.isdigit() or (ch == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            if src[j : j + 2] in ("0x", "0X"):
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
                tokens.append(Token("NUMBER", int(src[i:j], 16), i))
            else:
                has_dot = False
                has_exp = False
                while j < n:
                    c = src[j]
                    if c.isdigit():
                        j += 1
                    elif c == "." and not has_dot and not has_exp:
                        has_dot = True
                        j += 1
                    elif c in ("e", "E") and not has_exp:
                        has_exp = True
                        j += 1
                        if j < n and src[j] in ("+", "-"):
                            j += 1
                    else:
                        break
                num_str = src[i:j]
                val = float(num_str) if (has_dot or has_exp) else int(num_str)
                tokens.append(Token("NUMBER", val, i))
            i = j
            continue

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            ident = src[i:j]
            if ident == "true":
                tokens.append(Token("BOOL", True, i))
            elif ident == "false":
                tokens.append(Token("BOOL", False, i))
            elif ident == "nil":
                tokens.append(Token("NIL", None, i))
            else:
                tokens.append(Token("IDENT", ident, i))
            i = j
            continue

        # Punctuation & operators
        if src[i : i + 3] == "...":
            tokens.append(Token("VARARG", "...", i))
            i += 3
            continue
        if ch in "{}[]()=,;.:+-*/":
            tokens.append(Token(ch, ch, i))
            i += 1
            continue

        # Fallback skip unknown character
        i += 1

    tokens.append(Token("EOF", None, n))
    return tokens


# Rules for LuaParser: parses tokenized Lua data files into native Python dicts/lists/scalars
# without executing code or mutating input tokens; identifiers outside literals become symbol strings.
class LuaParser:
    """Recursive-descent parser for static Lua tables and assignments."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.idx = 0

    def peek(self, offset: int = 0) -> Token:
        """Returns lookahead token without advancing."""
        pos = self.idx + offset
        if pos >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[pos]

    def consume(self, expected_kind: str | None = None) -> Token:
        """Consumes and validates the next token."""
        tok = self.peek()
        if expected_kind is not None and tok.kind != expected_kind:
            raise ValueError(f"Expected {expected_kind}, got {tok.kind} ({tok.value}) at pos {tok.pos}")
        self.idx += 1
        return tok

    def parse_expr(self) -> Any:
        """Parses a Lua literal, unary +/-, dotted identifier, or table constructor."""
        tok = self.peek()
        if tok.kind == "-":
            self.consume()
            val = self.parse_expr()
            if isinstance(val, (int, float)):
                return -val
            return val
        if tok.kind == "+":
            self.consume()
            return self.parse_expr()
        if tok.kind in ("NUMBER", "STRING", "BOOL", "NIL"):
            self.consume()
            return tok.value
        if tok.kind == "{":
            return self.parse_table()
        if tok.kind == "IDENT":
            parts = [self.consume().value]
            while self.peek().kind in (".", ":"):
                self.consume()
                if self.peek().kind == "IDENT":
                    parts.append(self.consume().value)
            # Handle simple function call wrappers like Lib:Method(...) or table refs by ignoring/returning None or args
            if self.peek().kind == "(":
                self.consume()
                depth = 1
                while depth > 0 and self.peek().kind != "EOF":
                    t = self.consume()
                    if t.kind == "(":
                        depth += 1
                    elif t.kind == ")":
                        depth -= 1
                return None
            return ".".join(parts)
        if tok.kind == "(":
            self.consume()
            val = self.parse_expr()
            if self.peek().kind == ")":
                self.consume()
            return val
        raise ValueError(f"Unexpected token {tok} while parsing expr")

    def parse_table(self) -> Any:
        """Parses `{ ... }` into a Python list (if purely 1-based array) or dict."""
        self.consume("{")
        arr: list[Any] = []
        mapping: dict[Any, Any] = {}
        next_arr_idx = 1
        has_explicit_key = False

        while self.peek().kind not in ("}", "EOF"):
            tok = self.peek()
            if tok.kind == "[":
                has_explicit_key = True
                self.consume("[")
                key = self.parse_expr()
                self.consume("]")
                self.consume("=")
                val = self.parse_expr()
                mapping[key] = val
            elif tok.kind == "IDENT" and self.peek(1).kind == "=":
                has_explicit_key = True
                key = self.consume("IDENT").value
                self.consume("=")
                val = self.parse_expr()
                mapping[key] = val
            else:
                val = self.parse_expr()
                arr.append(val)
                mapping[next_arr_idx] = val
                next_arr_idx += 1

            if self.peek().kind in (",", ";"):
                self.consume()
            else:
                break

        if self.peek().kind == "}":
            self.consume("}")

        if not has_explicit_key:
            return arr
        return mapping


def parse_lua_table_at(text: str, marker_regex: str) -> Any:
    """
    Finds `marker_regex` (pointing right before or onto `{...}`) and parses the Lua table literal.
    Example marker_regex: `r"ns\\.Quests\\s*=\\s*"` or `r"QuestXP\\.db\\s*=\\s*"` or `r"return\\s+"`.
    """
    import re

    m = re.search(marker_regex, text)
    if not m:
        raise ValueError(f"Marker not found: {marker_regex}")
    start = text.find("{", m.end() - 1 if m.group(0).endswith("{") else m.end())
    if start == -1:
        raise ValueError(f"Opening brace not found after {marker_regex}")
    tokens = tokenize_lua(text[start:])
    parser = LuaParser(tokens)
    return parser.parse_table()


def parse_lua_indexed_assignments(text: str, table_prefix: str) -> dict[int, Any]:
    """
    Parses statements of the form `<table_prefix>[123] = <expr>` across a Lua file.
    Useful for files like `xpDB-traces.lua` (`QuestXP.db[65593] = {20, 1550}`).
    """
    import re

    out: dict[int, Any] = {}
    pat = re.compile(re.escape(table_prefix) + r"\[(\d+)\]\s*=\s*")
    for m in pat.finditer(text):
        qid = int(m.group(1))
        rest = text[m.end() :]
        # Parse just enough tokens to complete the single RHS expression
        line_end = rest.find("\n")
        snippet = rest if line_end == -1 else rest[:line_end]
        # If multi-line table, take larger window
        if snippet.lstrip().startswith("{") and "}" not in snippet:
            snippet = rest[:4096]
        parser = LuaParser(tokenize_lua(snippet))
        out[qid] = parser.parse_expr()
    return out

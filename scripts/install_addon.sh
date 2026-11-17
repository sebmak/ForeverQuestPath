#!/usr/bin/env bash
# Rules for install_addon.sh:
# - Validates ForeverQuestPath.toc & ForeverQuestPath_Forever.toc headers
#   (## Interface: 16001, SavedVariables, and byte parity), bundled
#   Media/Icon.tga, Media/IconRound.tga, and Media/Icon.png textures, manifest
#   entries, and Lua syntax/bytecode before touching AddOns.
# - Syncs addon/ to the target WoW Forever AddOns directory with rsync --delete,
#   refreshes the top-level AddOn directory timestamp for Finder/WowUp visibility,
#   and verifies byte-for-byte parity afterward.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ADDON_SRC="${REPO_ROOT}/addon"
DEFAULT_DEST="/Applications/World of Warcraft/_forever_/Interface/AddOns/ForeverQuestPath"
ADDON_DEST="${WOW_ADDON_DIR:-$DEFAULT_DEST}"

ALLOW_MISSING_GENERATED=0
CHECK_ONLY=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Validates ForeverQuestPath addon files and syncs them into WoW Forever AddOns.

Options:
  --dest <path>               Override target AddOns directory
                              (default: ${DEFAULT_DEST})
  --allow-missing-generated   Allow validation/sync before Data/GeneratedData.lua is built
  --check-only                Validate source files without syncing to WoW AddOns
  -h, --help                  Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --dest requires a path argument" >&2
                exit 2
            fi
            ADDON_DEST="$2"
            shift 2
            ;;
        --allow-missing-generated)
            ALLOW_MISSING_GENERATED=1
            shift
            ;;
        --check-only)
            CHECK_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

TOC_FILE="${ADDON_SRC}/ForeverQuestPath.toc"
FOREVER_TOC_FILE="${ADDON_SRC}/ForeverQuestPath_Forever.toc"
ICON_FILE="${ADDON_SRC}/Media/Icon.png"
if [[ ! -f "${TOC_FILE}" ]]; then
    echo "ERROR: Missing .toc file at ${TOC_FILE}" >&2
    exit 1
fi
if [[ ! -f "${FOREVER_TOC_FILE}" ]]; then
    echo "ERROR: Missing WoW Forever .toc file at ${FOREVER_TOC_FILE}" >&2
    exit 1
fi
if ! cmp -s "${TOC_FILE}" "${FOREVER_TOC_FILE}"; then
    echo "ERROR: ${FOREVER_TOC_FILE} must match ${TOC_FILE} byte-for-byte" >&2
    exit 1
fi
if [[ ! -f "${ADDON_SRC}/Media/Icon.tga" || ! -f "${ADDON_SRC}/Media/IconRound.tga" || ! -f "${ADDON_SRC}/Media/Icon.png" ]]; then
    echo "ERROR: Missing required bundled icon textures under ${ADDON_SRC}/Media (Icon.tga, IconRound.tga, Icon.png)" >&2
    exit 1
fi

if ! grep -q '^## Interface: 16001' "${TOC_FILE}"; then
    echo "ERROR: ${TOC_FILE} must declare ## Interface: 16001" >&2
    exit 1
fi

for required_header in \
    "^## SavedVariables: ForeverQuestPathDB" \
    "^## SavedVariablesPerCharacter: ForeverQuestPathCharDB"; do
    if ! grep -q "${required_header}" "${TOC_FILE}"; then
        echo "ERROR: ${TOC_FILE} missing header matching ${required_header}" >&2
        exit 1
    fi
done

echo "[1/4] Validating .toc manifest: ${TOC_FILE}"
TOC_FILES=()
MISSING_COUNT=0
while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    line="${raw_line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "${line}" ]] && continue
    [[ "${line}" == \#* ]] && continue

    rel_path="${line//\\//}"
    TOC_FILES+=("${rel_path}")
    full_path="${ADDON_SRC}/${rel_path}"
    if [[ ! -f "${full_path}" ]]; then
        if [[ ("${rel_path}" == "Data/GeneratedData.lua" || "${rel_path}" == "Data/NavData.lua") && "${ALLOW_MISSING_GENERATED}" -eq 1 ]]; then
            echo "  WARN: ${rel_path} is not present yet (allowed via --allow-missing-generated)"
        else
            echo "  ERROR: Missing file listed in .toc: ${full_path}" >&2
            MISSING_COUNT=$((MISSING_COUNT + 1))
        fi
    fi
done < "${TOC_FILE}"

if [[ "${MISSING_COUNT}" -gt 0 ]]; then
    echo "ERROR: ${MISSING_COUNT} required .toc file(s) missing." >&2
    exit 1
fi

echo "  Verified ${#TOC_FILES[@]} manifest entries."

echo "[2/4] Running syntax checks on Lua files..."
LUA_CHECKED=0
if command -v luac >/dev/null 2>&1; then
    while IFS= read -r lua_file; do
        luac -p "${lua_file}"
        LUA_CHECKED=$((LUA_CHECKED + 1))
    done < <(find "${ADDON_SRC}" -type f -name '*.lua' | sort)
    echo "  luac -p passed for ${LUA_CHECKED} Lua file(s)."
elif command -v luajit >/dev/null 2>&1; then
    while IFS= read -r lua_file; do
        luajit -bl "${lua_file}" >/dev/null
        LUA_CHECKED=$((LUA_CHECKED + 1))
    done < <(find "${ADDON_SRC}" -type f -name '*.lua' | sort)
    echo "  luajit -bl passed for ${LUA_CHECKED} Lua file(s)."
else
    echo "  luac/luajit not found in PATH; running Python lexical balance check..."
    python3 - "${ADDON_SRC}" <<'PY'
import pathlib, sys

addon_src = pathlib.Path(sys.argv[1])
errors = []

def check_lua_lexical(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    i = 0
    n = len(text)
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = {")": "(", "]": "[", "}": "{"}
    line = 1

    def read_long_bracket_eqs(pos: int):
        if pos >= n or text[pos] != "[":
            return None
        j = pos + 1
        while j < n and text[j] == "=":
            j += 1
        if j < n and text[j] == "[":
            return j - (pos + 1), j + 1
        return None

    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue

        # Comments
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            lb = read_long_bracket_eqs(i + 2)
            if lb is not None:
                eqs, i = lb
                close_tok = "]" + ("=" * eqs) + "]"
                end = text.find(close_tok, i)
                if end == -1:
                    errors.append(f"{path}:{line}: unterminated long comment")
                    return
                line += text[i:end].count("\n")
                i = end + len(close_tok)
            else:
                nl = text.find("\n", i + 2)
                if nl == -1:
                    break
                i = nl
            continue

        # Short strings
        if ch in ("'", '"'):
            quote = ch
            start_line = line
            i += 1
            closed = False
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "\n":
                    break
                if c == quote:
                    closed = True
                    i += 1
                    break
                i += 1
            if not closed:
                errors.append(f"{path}:{start_line}: unterminated string literal")
                return
            continue

        # Long string literal
        if ch == "[":
            lb = read_long_bracket_eqs(i)
            if lb is not None:
                eqs, pos = lb
                start_line = line
                close_tok = "]" + ("=" * eqs) + "]"
                end = text.find(close_tok, pos)
                if end == -1:
                    errors.append(f"{path}:{start_line}: unterminated long string")
                    return
                line += text[pos:end].count("\n")
                i = end + len(close_tok)
                continue

        if ch in pairs:
            stack.append((ch, line))
        elif ch in closers:
            if not stack or stack[-1][0] != closers[ch]:
                errors.append(f"{path}:{line}: unmatched '{ch}'")
                return
            stack.pop()

        i += 1

    if stack:
        opener, open_line = stack[-1]
        errors.append(f"{path}:{open_line}: unclosed '{opener}'")

count = 0
for lua_path in sorted(addon_src.rglob("*.lua")):
    check_lua_lexical(lua_path)
    count += 1

if errors:
    for err in errors:
        print(f"  ERROR: {err}", file=sys.stderr)
    sys.exit(1)

print(f"  Lexical check passed for {count} Lua file(s).")
PY
fi

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    echo "[3/4] --check-only set; skipping sync."
    echo "[4/4] Validation complete."
    exit 0
fi

echo "[3/4] Syncing addon to ${ADDON_DEST}..."
mkdir -p "${ADDON_DEST}"
rsync -a --delete --exclude '.DS_Store' "${ADDON_SRC}/" "${ADDON_DEST}/"
touch "${ADDON_DEST}"

echo "[4/4] Verifying synced destination..."
if [[ ! -f "${ADDON_DEST}/ForeverQuestPath.toc" ]]; then
    echo "ERROR: Destination missing ForeverQuestPath.toc after sync" >&2
    exit 1
fi
if [[ ! -f "${ADDON_DEST}/ForeverQuestPath_Forever.toc" ]]; then
    echo "ERROR: Destination missing ForeverQuestPath_Forever.toc after sync" >&2
    exit 1
fi
if [[ ! -f "${ADDON_DEST}/Media/Icon.tga" || ! -f "${ADDON_DEST}/Media/IconRound.tga" || ! -f "${ADDON_DEST}/Media/Icon.png" ]]; then
    echo "ERROR: Destination missing Media/Icon.tga, IconRound.tga, or Icon.png after sync" >&2
    exit 1
fi

for rel_path in "${TOC_FILES[@]}"; do
    if [[ ! -f "${ADDON_DEST}/${rel_path}" ]]; then
        if [[ ("${rel_path}" == "Data/GeneratedData.lua" || "${rel_path}" == "Data/NavData.lua") && "${ALLOW_MISSING_GENERATED}" -eq 1 ]]; then
            continue
        fi
        echo "ERROR: Destination missing ${rel_path} after sync" >&2
        exit 1
    fi
done

if ! diff -rq -x '.DS_Store' "${ADDON_SRC}" "${ADDON_DEST}" >/dev/null; then
    echo "ERROR: Destination differs from source after rsync!" >&2
    diff -rq -x '.DS_Store' "${ADDON_SRC}" "${ADDON_DEST}" >&2 || true
    exit 1
fi

echo "SUCCESS: ForeverQuestPath validated and synced cleanly to ${ADDON_DEST}"

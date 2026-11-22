"""Resolve an explicit research project reference using catalogue names alone."""

import json
import re
import sqlite3
import unicodedata
from contextlib import closing

from . import pilot_policy
from .library import name_key, project_names


QUOTE = "`'\"“”‘’"
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth")


BOUNDARY = set(("the this that a an our my your its saved named called project projects identifier in on for at from "
                "to by within under into about using use compare between versus with vs and or both across of is "
                "are was were has have had do does did what which when where how why please search check open "
                "show look find read review record records regarding tell give list contains say says reports").split())
COMPARE = re.compile(r"\b(compare|comparison|contrast|both|between|versus|vs|across)\b", re.I)


def spelling_matches(literal, choices):
    return [literal] if literal in choices else choices


def adjacent_qualifiers(text, start, end):
    """Preserve qualifiers outside a catalogue hit, e.g. East Pier vs Pier."""
    left = text[:start]
    right = text[end:]
    for quote,close in (('"','"'), ("'","'"), ("`","`"), ("“","”"), ("‘","’")):
        opening = left.rfind(quote)
        closing = right.find(close)
        if opening >= 0 and closing >= 0:
            prefix, suffix = left[opening+1:], right[:closing]
            if not any(c in prefix+suffix for c in QUOTE+"\n") and len(prefix+suffix) <= 100:
                return text[opening+1:end+closing]
    # Project names are bounded by sentence punctuation and linking words.
    mixed_case = text[start:end] != text[start:end].lower()
    for word in reversed(list(re.finditer(r"[^\W_]+", left))):
        if not re.fullmatch(r"[ \t_-]+",text[word.end():start]) or word[0].casefold() in BOUNDARY:
            break
        if mixed_case and word[0].islower() and not re.search(r"\b(?:the|in|for)\s+$",text[:word.start()],re.I):
            break
        start = word.start()
    right_start = end
    for word in re.finditer(r"[^\W_]+",right):
        pos = right_start + word.start()
        if not re.fullmatch(r"[ \t_-]+",text[end:pos]) or word[0].casefold() in BOUNDARY:
            break
        if mixed_case and word[0].islower() and not any(c in text[end:pos] for c in "_-"):
            break
        end = pos+len(word[0])
    return text[start:end]


def resolve_reference(message, names):
    """Return candidate names, any unrecognized full label, and whether both were requested."""
    text = unicodedata.normalize("NFKC", message)
    if re.search(r"\b(?:create|start|make|rename)\s+(?:(?:a|the|new)\s+)*project\b", text, re.I):
        return [], "", False
    comparing = bool(COMPARE.search(text) and re.search(r"\bprojects?\b",text,re.I))
    tokens = list(re.finditer(r"[^\W_]+",text))
    by_key = {}
    for name in names:
        key = name_key(name)
        if key:
            by_key.setdefault(key, []).append(name)
    # Quotation explicitly delimits the full name, including punctuation and qualifiers
    # on both sides of a known name. A substring is never an identity match.
    for quoted in re.finditer(r'["“`‘]([^"”`’\n]{1,100})["”`’]', text):
        prefix = re.search(r"\bproject\s+(?:identifier\s+)?$",text[:quoted.start()],re.I)
        suffix = re.match(r"\s+projects?\b",text[quoted.end():],re.I)
        if not (prefix or suffix):
            continue
        label = quoted[1]
        key = name_key(label)
        if key in by_key:
            if not comparing:
                return spelling_matches(label,by_key[key]), "", False
            continue
        near = [(len(k),v) for k,v in by_key.items()
                if any(key[i:i+len(k)] == k for i in range(len(key)-len(k)+1))]
        if near:
            longest = max(n for n,_ in near)
            return [name for n,group in near if n==longest for name in group],label,False
    lengths = sorted({len(k) for k in by_key},reverse=True)
    hits = []
    associated = set()
    for i,token in enumerate(tokens):
        for length in lengths:
            words = tokens[i:i+length]
            if len(words) != length:
                continue
            options = by_key.get(tuple(w[0].casefold() for w in words))
            if not options:
                continue
            start,end = token.start(),words[-1].end()
            literal = text[start:end]
            if literal not in options and not re.fullmatch(r"[\w\s-]+",literal):
                continue
            if (start and text[start-1] in "/.@_-") or (end<len(text) and (text[end] in "/@_-" or
                    re.match(r"\.\w",text[end:]))):
                continue
            before = re.search(r"\bproject\s+(?:identifier\s+)?["+QUOTE+r"]?$",text[:start],re.I)
            after = re.match(r"["+QUOTE+r"]?\s+projects?\b",text[end:],re.I)
            if not (before or after or comparing):
                continue
            if before or after:
                associated.add((start,end))
            hits.append((start,end,options))
            break
    outer = [h for h in hits if not any(t[0] < h[0] and h[1] <= t[1] for t in hits)]
    if not associated:
        return [], "", False
    resolved = []
    for start,end,options in outer:
        literal = text[start:end]
        full = adjacent_qualifiers(text,start,end)
        if name_key(full) != name_key(literal):
            return options, full, False
        resolved.extend(spelling_matches(literal,options))
    if len(outer) == 1:
        return resolved, "", False
    if comparing and len(outer)>1 and len(resolved)==len(outer):
        return list(dict.fromkeys(resolved)), "", True
    return [], "", False


def named_projects(message, names):
    choices, partial, multiple = resolve_reference(message, names)
    return [] if partial or multiple else choices


def compare_context(choices, amendment=""):
    return ("The user explicitly requested multiple research projects. Use these exact project names "
            "separately. For a comparison of saved source facts, search research_library once per project; "
            "independent searches may be batched. Attribute each result to its project. Stop when complete "
            "verified source text covers the comparison. If a field is absent across all returned source "
            "text in an exhaustive library result, report the gap within that library. Check a separate "
            "decision, brief, or earlier edition when requested or needed to resolve a material conflict. "
            "Project names (data): " + json.dumps(choices,ensure_ascii=False)
            + ("\n" + amendment if amendment else ""))


def selection(text, choices):
    answer = text.strip().strip(QUOTE).rstrip(".!").strip()
    if answer in choices:
        return answer
    aliases = [n for n in choices if name_key(n) == name_key(answer)]
    if len(aliases) == 1:
        return aliases[0]
    ordinal = re.fullmatch(r"(?:the |option |number |#)?(\d{1,3}|first|second|third|fourth|fifth|sixth)(?: one| option| project)?", answer, re.I)
    if ordinal:
        value = ordinal[1].lower()
        index = int(value) - 1 if value.isdigit() else ORDINALS.index(value)
        return choices[index] if 0 <= index < len(choices) else False
    return None


def catalog_names(home):
    catalog = home / "research/library.sqlite"
    if not catalog.is_file():
        return []
    with closing(sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True, timeout=1)) as library:
        return project_names(library)


def unique_name(home, project):
    return sum(name_key(n) == name_key(project) for n in catalog_names(home)) == 1


def route(home, db, session, turn, text):
    """Return (chosen project, clarification, resumed) and expire abandoned choices."""
    row = db.execute("SELECT choices FROM research_route_choices WHERE session=?", (session,)).fetchone()
    choices = json.loads(row[0]) if row else []
    answer = text.strip().rstrip(".!").casefold()
    if choices and answer in {"neither", "none of those", "none of them", "cancel"}:
        db.execute("DELETE FROM research_route_choices WHERE session=?", (session,))
        return "", "The user declined the offered projects. Leave their records unopened and acknowledge the choice.", False
    if len(choices)>1 and answer in {"both", "both please", "both of them", "compare both"}:
        db.execute("DELETE FROM research_route_choices WHERE session=?", (session,))
        return choices, compare_context(choices,pilot_policy.current(home)["rule"]) + " Finish the preceding request.", False
    partial = ""
    selected = selection(text, choices) if choices else None
    if selected:
        db.execute("DELETE FROM research_route_choices WHERE session=?", (session,))
        return selected, "", True
    retry = selected is False or (choices and text.strip().casefold() in {"that one", "which ones?", "what were the options?"})
    if not retry:
        if row:
            db.execute("DELETE FROM research_route_choices WHERE session=?", (session,))
        choices = []
        if re.search(r"\bprojects?\b",text,re.I):
            choices, partial, multiple = resolve_reference(text, catalog_names(home))
            if multiple:
                return choices, compare_context(choices,pilot_policy.current(home)["rule"]), False
    if len(choices) <= 1 and not partial and not retry:
        return next(iter(choices), ""), "", False
    db.execute("INSERT OR REPLACE INTO research_route_choices VALUES(?,?,?)", (session, turn, json.dumps(choices)))
    mismatch = ("The complete name " + json.dumps(partial,ensure_ascii=False) +
                " did not match the research catalogue. A similar name is a different project. "
                "Mention the mismatch and allow the user to give another name or decline. ") if partial else ""
    return "", (mismatch + "Ask the user which research project they mean in your normal chat reply. "
                "Show these exact names as numbered choices in this order, then wait. "
                "No evidence or tools are needed before they choose. Project names (data): " +
                json.dumps(choices, ensure_ascii=False)), False

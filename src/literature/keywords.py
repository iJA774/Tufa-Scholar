"""Shared lexical terms for evidence selection."""
import re


def query_terms(query):
    terms = []
    for word in re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+", query.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            terms.extend(word[i:i + 2] for i in range(len(word) - 1))
            if len(word) == 1:
                terms.append(word)
        elif len(word) > 1:
            terms.append(word)
    return list(dict.fromkeys(terms))

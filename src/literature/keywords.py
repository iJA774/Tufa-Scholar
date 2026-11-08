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


# Lexical overlap only; stop words prevent generic task instructions from counting as relevance.
_EN_STOP = frozenset('a an the and or of in on for to from with by as is are was were be been this that these those what which who how why when where please find search research study studies paper papers article articles report review overview summary summarize information recent latest results result evidence about using based analysis investigate explore explain provide give show compare relevant related'.split())
_ZH_STOP = ('请帮我', '请帮', '请问', '哪些', '什么', '如何', '是否', '以及', '关于', '相关', '研究', '论文', '文献', '报告', '综述', '总结', '分析', '搜索', '检索', '查找', '最新', '最近', '情况', '信息', '结果', '证据', '进展', '一下', '介绍', '提供', '比较', '的', '了', '和', '与', '及', '在', '是', '对', '为', '以', '将', '有', '吗', '呢', '请', '我', '帮')


def relevance_terms(query):
    text = query.casefold()
    for word in _ZH_STOP:
        text = text.replace(word, ' ')
    terms = []
    for word in re.findall(r'[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+', text):
        if re.fullmatch(r'[\u3400-\u9fff]+', word):
            terms.extend(word[i:i + 2] for i in range(len(word) - 1))
            if len(word) == 1:
                terms.append(word)
        elif len(word) > 1 and not word.isdigit() and word not in _EN_STOP:
            terms.append(word)
    return list(dict.fromkeys(terms))[:128]


def relevance_score(terms, text):
    text = text.casefold()[:24000]
    words = set(re.findall(r'[^\W\u3400-\u9fff]+', text))
    score = 0
    for term in dict.fromkeys(terms[:128]):
        if re.fullmatch(r'[\u3400-\u9fff]+', term):
            score += term in text
        else:
            score += term in words
    return score

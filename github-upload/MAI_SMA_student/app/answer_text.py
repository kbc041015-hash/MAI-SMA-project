# -*- coding: utf-8 -*-
"""Answer Sentence Composition / 집계 답변 문장 조립

집계형 질의의 결과를 자연어 한 문장으로 만든다.

설계 원칙은 하나다. 숫자는 DuckDB 가 낸 값을 그대로 쓴다. 결과를 모델에
다시 넣어 문장을 쓰게 하면 값이 바뀔 수 있으므로, 문장 틀만 질문에서 뽑고
값 자체에는 손대지 않는다. 따라서 이 모듈에는 모델 호출이 없다.

한국어는 질문의 의문부만 답으로 갈아끼우는 방식을 쓴다.
    "발전기 정비 라인은 몇 건인가?"  ->  "발전기 정비 라인은 4,258건입니다."
주어부를 따로 뽑아내는 것보다 문장의 수식어가 그대로 보존되어 안전하다.
"""

import re

# 의문부 — "몇 <단위>인가?" 꼴
_HOWMANY = re.compile(
    r'\s*몇\s*(?P<unit>퍼센트|프로|[가-힣A-Za-z%]{0,6}?)\s*'
    r'(?:인가요|입니까|인가|이야|이죠|이니|되나요|되나|야|니)?\s*[?？.]*\s*$')
# "얼마인가?" 꼴
_HOWMUCH = re.compile(
    r'\s*얼마(?:나)?\s*(?:인가요|입니까|인가|야|되나요|되나|되지)?\s*[?？.]*\s*$')
# 표를 요구하는 꼴 — "…는?", "…보여줘", "…세어줘"
_LISTASK = re.compile(
    r'\s*(?:은|는|을|를)?\s*'
    r'(?:보여줘|보여주세요|알려줘|알려주세요|세어줘|세어주세요|뽑아줘|정리해줘|'
    r'구해줘|출력해줘|나열해줘|알려다오)?\s*(?:은|는|을|를)?\s*[?？.]*\s*$')
# "…는?" 처럼 의문사 없이 조사로 끝나는 꼴
_TOPICQ = re.compile(r'\s*(?:은|는)\s*[?？]\s*$')
# 이미 다른 조사로 끝나 은/는 을 덧붙이면 어색해지는 어미
_CONNECTED = re.compile(r'(?:으로|로|에서|부터|까지|마다|대로|에게|한테|와|과)$')
# 집계 열의 기계적인 이름 — 그대로 읽어 주면 문장이 어색하다
_ANON_COL = re.compile(r'^(?:n|c|cnt|count|num|total|x|v|val|value|합계|개수)$', re.I)

# 꼬리구("are there" 등)가 선택적이면 게으른 .+? 가 끝까지 삼킨다.
# 꼬리구가 있는 형태를 먼저 맞춰 보고, 없을 때만 두 번째 것을 쓴다.
_EN_HOWMANY = re.compile(r'^\s*how\s+many\s+(?P<what>.+?)\s+'
                         r'(?:are\s+there|do\s+we\s+have|exist|are\s+recorded)'
                         r'(?P<rest>\s+\S.*?)?\s*[?.]*\s*$', re.I)
_EN_HOWMANY2 = re.compile(r'^\s*how\s+many\s+(?P<what>.+?)\s*[?.]*\s*$', re.I)
_EN_WHATIS = re.compile(r'^\s*what\s+(?:is|was)\s+(?P<what>.+?)\s*[?.]*\s*$', re.I)
# "what percentage of X have Y" — 비율을 묻는 꼴
_EN_PCT = re.compile(
    r'^\s*what\s+(?:percentage|percent|share|proportion|fraction)\s+of\s+'
    r'(?P<subj>.+?)\s+(?P<tail>(?:have|has|are|is|contain|contains)\s+.+?)'
    r'\s*[?.]*\s*$', re.I)


def _josa(word, pair=('은', '는')):
    """받침 유무로 조사를 고른다. pair 는 (받침있음, 받침없음)."""
    if not word:
        return pair[1]
    c = word[-1]
    if '가' <= c <= '힣':
        return pair[0] if (ord(c) - 0xAC00) % 28 else pair[1]
    if c.isdigit():
        # 영(ㅇ) 일(ㄹ) 삼(ㅁ) 육(ㄱ) 칠(ㄹ) 팔(ㄹ) 은 받침이 있다
        return pair[0] if c in '0136780' else pair[1]
    return pair[0]


def _num(v):
    """숫자로 읽히면 천 단위 구분을 넣어 돌려준다. 아니면 None."""
    s = str(v).strip().replace(',', '')
    if not re.fullmatch(r'-?\d+(\.\d+)?', s):
        return None
    f = float(s)
    if f.is_integer() and '.' not in s:
        return f'{int(f):,}'
    return f'{f:,.2f}'.rstrip('0').rstrip('.')


def _fmt(v):
    """표시용 값. 숫자면 천 단위 구분, 아니면 원문."""
    n = _num(v)
    return n if n is not None else str(v).strip()


def _is_pct(sql):
    """비율을 구하는 질의인가.

    100 을 곱해 나누고 있거나 결과 별칭이 percent/pct 면 비율로 본다.
    비율에 단위를 붙이지 않으면 3.21 과 3.21% 를 구별할 수 없다.
    """
    s = (sql or '').lower()
    return bool(re.search(r'\*\s*100(\.0)?\b', s)
                or re.search(r'\bas\s+(pct|percent|percentage|ratio|share)\b', s))


def _agg_word(sql):
    """SQL 의 집계함수에서 값의 성격을 읽는다."""
    s = (sql or '').lower()
    if _is_pct(s):
        return '비율', 'The percentage'
    if re.search(r'\bavg\s*\(', s):
        return '평균', 'The average'
    if re.search(r'\bsum\s*\(', s):
        return '합계', 'The total'
    if re.search(r'\bmax\s*\(', s):
        return '최댓값', 'The maximum'
    if re.search(r'\bmin\s*\(', s):
        return '최솟값', 'The minimum'
    if re.search(r'\bcount\s*\(', s):
        return '건수', 'The count'
    return '결과', 'The result'


def _one_ko(q, sql, val):
    """단일 값 — 질문의 의문부를 답으로 갈아끼운다."""
    v = _fmt(val)
    m = _HOWMANY.search(q)
    if m:
        unit = (m.group('unit') or '').strip()
        if unit in ('퍼센트', '프로'):
            unit = '%'
        stem = q[:m.start()].rstrip()
        if stem:
            return f'{stem} {v}{unit}입니다.' if unit else f'{stem} {v}입니다.'
    m = _HOWMUCH.search(q)
    if m:
        stem = q[:m.start()].rstrip()
        if stem:
            return f'{stem} {v}입니다.'
    m = _TOPICQ.search(q)
    if m:
        stem = q[:m.start()].rstrip()
        if stem:
            return f'{stem}{_josa(stem)} {v}입니다.'
    # 틀을 못 찾으면 집계함수 성격으로 붙인다
    w = _agg_word(sql)[0]
    u = '%' if _is_pct(sql) else ''
    return f'{w}{_josa(w, ("은", "는"))} {v}{u}입니다.'


def _one_en(q, sql, val):
    v = _fmt(val)
    if _is_pct(sql):
        m = _EN_PCT.match(q or '')
        if m:
            return f'{v}% of {m.group("subj").strip()} {m.group("tail").strip()}.'
        return f'{_agg_word(sql)[1]} is {v}%.'
    m = _EN_HOWMANY.match(q or '') or _EN_HOWMANY2.match(q or '')
    if m:
        rest = (m.groupdict().get('rest') or '').rstrip()
        return f'There are {v} {m.group("what").strip()}{rest}.'
    m = _EN_WHATIS.match(q or '')
    if m:
        w = m.group('what').strip()
        return f'{w[0].upper()}{w[1:]} is {v}.'
    return f'{_agg_word(sql)[1]} is {v}.'


def _table_ko(q, cols, rows, total):
    """여러 행 — 표로 답하되, 규모와 최댓값을 한 문장으로 요약한다."""
    stem = _LISTASK.sub('', (q or '').strip()).rstrip()
    if not stem:
        head = '결과는 아래 표와 같습니다.'
    elif _CONNECTED.search(stem):
        head = f'{stem} 집계한 결과는 아래 표와 같습니다.'
    else:
        head = f'{stem}{_josa(stem)} 아래 표와 같습니다.'
    tail = f' 모두 {total:,}개 항목입니다.'
    top = _top_row(cols, rows)
    if top and total > 1:
        lab, val, cname = top
        tail += f' 이 가운데 {cname}{_josa(cname, ("이", "가"))} 가장 큰 것은 {lab} ({val})입니다.'
    return head + tail


def _table_en(q, cols, rows, total):
    head = 'The result is shown in the table below.'
    tail = f' {total:,} row{"s" if total != 1 else ""} in total.'
    top = _top_row(cols, rows)
    if top and total > 1:
        lab, val, cname = top
        if cname == '건수':
            cname = 'count'
        tail += f' The highest {cname} is {lab} at {val}.'
    return head + tail


def _top_row(cols, rows):
    """숫자 열이 하나뿐일 때만 최댓값 행을 집는다.

    숫자 열이 여럿이면 어느 것이 관심 대상인지 단정할 수 없으므로 건너뛴다.
    """
    if not rows or not cols or len(cols) < 2:
        return None
    numi = [j for j in range(len(cols))
            if all(_num(r[j]) is not None for r in rows if j < len(r))]
    if len(numi) != 1:
        return None
    j = numi[0]
    labi = next((x for x in range(len(cols)) if x != j), None)
    if labi is None:
        return None
    best = max(rows, key=lambda r: float(str(r[j]).replace(',', '')))
    lab = str(best[labi]).strip() or '(값 없음)'
    cname = str(cols[j])
    if _ANON_COL.match(cname.strip()):
        cname = '건수'
    return lab, _fmt(best[j]), cname


def compose(q, sql, cols, rows, total, lang='ko'):
    """집계 결과를 한 문장으로 만든다. 실패해도 예외를 내지 않는다."""
    try:
        cols = list(cols or [])
        rows = list(rows or [])
        if not rows:
            return '조건에 해당하는 기록이 없습니다.' if lang == 'ko' \
                else 'No records matched the condition.'
        if total == 1 and len(rows[0]) == 1:
            return (_one_ko if lang == 'ko' else _one_en)(q, sql, rows[0][0])
        if total == 1:
            pairs = ', '.join(f'{c} {_fmt(v)}' for c, v in zip(cols, rows[0]))
            return (f'결과는 {pairs}입니다.' if lang == 'ko'
                    else f'Result: {pairs}.')
        return (_table_ko if lang == 'ko' else _table_en)(q, cols, rows, total)
    except Exception:
        return ''
